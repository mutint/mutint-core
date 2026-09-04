"""Applying mutation edits, and reconstructing what the mutations were at any earlier point.

Three ideas, in order of how much they depend on each other.

**A change is a call appearing or disappearing.** `apply_edits` is the only writer
of `MutationCall` in this app, and it writes the log in the same transaction as the rows,
so there is no state in which a row moved and the log did not.

**A version is an edit set.** The rows in an edit set are what that version did. There is no
snapshot table -- the state at a version is *derived*, by taking the live state and undoing
every edit set newer than it, newest first. That is `state_after`. Snapshots would have to be
written on every edit and kept consistent with a log that already says the same thing.

**Restoring is a new change, not a rewind.** `restore` diffs the live state against
`state_after(...)` and applies the difference as a fresh edit set with `kind=RESTORE`. The log
is never rewritten, restoring twice to the same point is a no-op the second time, and a restore
can itself be restored past -- because by the time you look back at it, it is just another
edit set with rows in it.

The identity of a call, throughout, is `(sample_id, mutation_key, source)` and
deliberately **not** its primary key. A restored row is a new row with a new pk, and the
Mutation it points at may itself have been recreated -- see `mutation_key`. Two calls
can legitimately share that key (nothing in the schema forbids it), so the state maps a key to
a *list* and the diff compares lengths.

Edit sets are ordered by **primary key, not by `created_at`**, wherever "newer than" is being
decided. The timestamp is what a person picks a version by and is what the history page shows;
the pk is what guarantees a total order, which two edit sets written in the same microsecond
would otherwise not have.
"""

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from aledb_experiment.permissions import ExperimentLocked
from aledb_mutation_editor.models import (
    KIND_EDIT, KIND_RESTORE, OP_ADD, OP_REMOVE,
    MutationEdit, MutationEditSet,
)
from aledb_seq.models import Mutation, MutationCall
from aledb_experiment import paths

logger = logging.getLogger(__name__)

#: Every column of MutationCall except the two foreign keys and the pk. Snapshotted whole
#: so a removal can be undone exactly: a restore that guessed at `frequency` or dropped the
#: caller's evidence would put back a row that renders differently from the one deleted.
#:
#: `evidence` is one entry here where four used to be, and the list is what keeps that from
#: mattering to stored blobs: `_call_kwargs` builds its kwargs by walking this tuple and
#: calling `snapshot.get(field)`, so the retired keys left behind in older
#: `MutationEdit.snapshot` blobs are simply never read again, and a blob written before
#: `evidence` existed restores with it null. No migration either way.
CALL_FIELDS = (
    "present", "evidence", "frequency", "source",
)

#: The fields `gd_import._database_gd_mutations` passes to `Mutation.objects.get_or_create`.
#: Together with the experiment they are a mutation's identity, and they are what lets a
#: swept Mutation row be recreated as the same mutation rather than as a new one.
MUTATION_KEY_FIELDS = (
    "position", "reseq_reference", "mutation_type", "feature_length",
    "sequence_change", "gene",
)

_DECIMAL_FIELDS = ("frequency",)

#: The join from a MutationCall up to its experiment. Spelled once here; it is the same
#: traversal `aledb_seq.util` and `aledb_filter.util` use.
_EXPERIMENT_PATH = paths.to_experiment(paths.FROM_CALL)


# --- snapshotting ---------------------------------------------------------------------------


def call_snapshot(call):
    """Every column of a MutationCall, JSON-safe.

    `frequency` is a `DecimalField`, which JSON cannot carry, so it is stored as a string and
    rebuilt with `Decimal(...)` rather than through `float` -- a round trip through float would
    move the value at the fourth decimal place, which is exactly where that column keeps its
    precision.
    """
    snapshot = {}
    for field in CALL_FIELDS:
        value = getattr(call, field)
        if field in _DECIMAL_FIELDS and value is not None:
            value = str(value)
        snapshot[field] = value
    return snapshot


def _call_kwargs(snapshot):
    """A snapshot back into constructor kwargs."""
    kwargs = {}
    for field in CALL_FIELDS:
        value = snapshot.get(field)
        if field in _DECIMAL_FIELDS and value is not None:
            try:
                value = Decimal(str(value))
            except (InvalidOperation, ValueError):
                value = None
        kwargs[field] = value
    return kwargs


def mutation_identity(mutation):
    """Enough of a Mutation to recreate it as *the same* mutation.

    The `get_or_create` key plus the columns that carry everything renderable. `gd_data` is
    what `to_gd_line()` round-trips for `gdtools APPLY` and `annotation` is what the
    breseq-style tables render from, so a mutation recreated without them would come back
    displayable-but-degraded rather than identical.
    """
    identity = {field: getattr(mutation, field) for field in MUTATION_KEY_FIELDS}
    identity["gd_data"] = mutation.gd_data
    identity["annotation"] = mutation.annotation
    identity["product"] = mutation.product
    identity["protein_change"] = mutation.protein_change
    return identity


def mutation_key(mutation):
    """A mutation's stable identity, as a hashable tuple.

    Deliberately not the primary key. `_delete_all_orphaned_mutations` can sweep a Mutation
    whose last call this app removed, and a later restore recreates it through
    `get_or_create` -- with a *new* pk for the same biological mutation. Keying the state map
    on pk would make that restore look like a different mutation entirely.
    """
    return tuple(getattr(mutation, field) for field in MUTATION_KEY_FIELDS)


def key_from_identity(identity):
    """The same key, from a logged `mutation_identity` blob rather than a live row.

    JSON round-tripping turns tuples into lists and leaves everything else alone, so this
    rebuilds the tuple explicitly rather than trusting whatever came back.
    """
    return tuple(identity.get(field) for field in MUTATION_KEY_FIELDS)


def _entry(sample_id, identity, snapshot, mutation=None, call=None,
           source_sample_id=None):
    """One call in a state map -- live or reconstructed."""
    return {
        "sample_id": sample_id,
        "identity": identity,
        "snapshot": snapshot,
        "mutation": mutation,
        "call": call,
        "source_sample_id": source_sample_id,
    }


def _entry_key(entry):
    return (entry["sample_id"], key_from_identity(entry["identity"]),
            entry["snapshot"].get("source"))


# --- applying -------------------------------------------------------------------------------


def mutation_for_identity(experiment, identity):
    """The experiment's row holding `identity`, minted if it has none. `(row, created)`.

    `get_or_create` on exactly the fields `gd_import` keys on, so a row minted here is the row
    a re-import would have produced -- and a later import finds it rather than adding a second.
    That single behaviour is what both callers want and why it is one function: `_resolve_mutation`
    puts a swept mutation back, and `mutation_edit_apply` moves calls onto the row that
    already holds the corrected values or onto a new one, which is the same question asked with
    a different motive.

    `created` matters to a caller because the promoted annotation columns are not part of the
    identity: a row minted here has them empty and needs `record_builder.apply_annotation`,
    while one that was already here keeps what it has.
    """
    lookup = {field: identity.get(field) for field in MUTATION_KEY_FIELDS}
    return Mutation.objects.get_or_create(
        experiment=experiment,
        defaults={
            "gd_data": identity.get("gd_data"),
            "annotation": identity.get("annotation"),
            "product": identity.get("product") or "",
            "protein_change": identity.get("protein_change") or "",
        },
        **lookup)


def _resolve_mutation(experiment, mutation, identity):
    """The Mutation row an addition should point at, recreating it if it has been swept."""
    if mutation is not None and mutation.pk is not None:
        live = Mutation.objects.filter(pk=mutation.pk).first()
        # The row has to still *be* the mutation the identity describes. It is not enough that
        # it exists: `apply_mutation_edit` moves a row's fields while keeping its primary key,
        # so a restore to before an edit arrives here holding the old identity and a row that
        # has since become something else. Returning it would put the call back on the
        # edited mutation and report success, leaving the restore silently undone.
        if live is not None and mutation_key(live) == key_from_identity(identity):
            return live

    recreated, created = mutation_for_identity(experiment, identity)
    if created:
        logger.info("recreated mutation %s for experiment %s from a change-log snapshot",
                    recreated.pk, experiment.id)
    return recreated


@transaction.atomic
def apply_edits(experiment, user, kind, removals=(), additions=(), note="",
                  restored_to=None):
    """Write an edit set and make it true. The only writer of MutationCall here.

    removals   MutationCall instances to delete.
    additions  entry dicts (see `_entry`): the target sample id, the mutation identity, the
               call snapshot, and optionally the live Mutation and a source sample.

    Returns the edit set, or None if there was nothing to do. An empty edit set would be a
    history entry recording that nothing happened -- noise on the page, and a step
    `state_after` would walk for no reason.

    The rebuild is deliberately *not* done here; `rebuild_after_edit` runs it outside this
    transaction, for the reason `gd_import` runs its own after committing: a rebuild is long
    and reads what was just written, and holding a write transaction open across it would make
    every concurrent edit queue behind it.
    """
    # Defence in depth. Every caller checks permission first, and this trusts none of them:
    # it is the lowest layer that still knows which experiment it is writing to, and a new
    # write path added later is exactly the thing that forgets. Raising rather than returning
    # None because a silent no-op here would read as "there was nothing to do".
    if experiment is not None and experiment.is_locked:
        raise ExperimentLocked(experiment.lock_message())

    removals = list(removals)
    additions = list(additions)
    if not removals and not additions:
        return None

    edit_set = MutationEditSet.objects.create(
        experiment=experiment,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        kind=kind,
        note=note,
        restored_to=restored_to)

    edits = []

    for call in removals:
        edits.append(MutationEdit(
            edit_set=edit_set,
            operation=OP_REMOVE,
            sample_id=call.sample_id,
            mutation=call.mutation,
            snapshot=call_snapshot(call),
            mutation_identity=mutation_identity(call.mutation)))

    for entry in additions:
        identity = entry["identity"]
        resolved = _resolve_mutation(experiment, entry.get("mutation"), identity)
        created = MutationCall.objects.create(
            sample_id=entry["sample_id"],
            mutation=resolved,
            **_call_kwargs(entry["snapshot"]))
        edits.append(MutationEdit(
            edit_set=edit_set,
            operation=OP_ADD,
            sample_id=entry["sample_id"],
            mutation=resolved,
            source_sample_id=entry.get("source_sample_id"),
            snapshot=call_snapshot(created),
            mutation_identity=identity))

    if removals:
        MutationCall.objects.filter(
            pk__in=[call.pk for call in removals]).delete()

    MutationEdit.objects.bulk_create(edits)
    return edit_set


@transaction.atomic
def apply_mutation_edit(experiment, user, mutation, identity, note=""):
    """Change a mutation's own fields, everywhere it is observed. One edit set.

    The calls are logged as **removed and re-added**, which is not bookkeeping: the log
    is keyed on `(sample_id, mutation_key, source)`, and `mutation_key` is derived from the six
    identity fields. Move a Mutation without saying so and `live_state` starts computing a
    different key than every earlier entry recorded, with no edit set for `state_after` to
    undo -- so "restore to before the edit" would silently leave the edit in place.

    What is *not* re-created is the Mutation row. `_resolve_mutation` returns the row it is
    handed, so the additions point back at the one the removals came off, and its primary key
    never moves. That matters because Mutation ids are stored as bare integers, with no foreign
    key, in aledb-phylogeny's `branch_mutations` and in every exported CSV -- and nothing
    refreshes them. What aledb-phylogeny does on a mutation edit is throw its cached trees
    away, which corrects the ids it held by no longer holding them; an exported CSV is not
    even reachable to correct. Minting a new row would leave all of that pointing at a
    mutation with no calls; reusing it leaves them resolving, to the corrected call.

    The order below is the whole of this function. The removal snapshots have to be taken
    **before** the row moves, or both sides of the edit set would record the new identity and
    `state_after` would read the edit as having changed nothing.
    """
    if experiment is not None and experiment.is_locked:
        raise ExperimentLocked(experiment.lock_message())

    calls = list(calls_for(experiment).filter(mutation=mutation))
    if not calls:
        # Nothing observes it, so there is no state to move and nothing to log. The row is
        # left alone rather than edited in silence.
        return None

    before = mutation_identity(mutation)
    edit_set = MutationEditSet.objects.create(
        experiment=experiment,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        kind=KIND_EDIT,
        note=note)

    edits = [
        MutationEdit(
            edit_set=edit_set,
            operation=OP_REMOVE,
            sample_id=call.sample_id,
            mutation=mutation,
            snapshot=call_snapshot(call),
            mutation_identity=before)
        for call in calls
    ]

    for field in MUTATION_KEY_FIELDS:
        setattr(mutation, field, identity.get(field))
    mutation.gd_data = identity.get("gd_data")
    mutation.annotation = identity.get("annotation")
    mutation.product = identity.get("product") or ""
    mutation.protein_change = identity.get("protein_change") or ""
    mutation.save()

    for call in calls:
        created = MutationCall.objects.create(
            sample_id=call.sample_id,
            mutation=mutation,
            **_call_kwargs(call_snapshot(call)))
        edits.append(MutationEdit(
            edit_set=edit_set,
            operation=OP_ADD,
            sample_id=call.sample_id,
            mutation=mutation,
            snapshot=call_snapshot(created),
            mutation_identity=identity))

    MutationCall.objects.filter(
        pk__in=[call.pk for call in calls]).delete()
    MutationEdit.objects.bulk_create(edits)
    return edit_set


def rebuild_after_edit(experiment):
    """Recompute what an edit invalidated. Call outside the transaction.

    **Unnarrowed by name, and narrowed by scope.** Both halves matter and they are different
    questions.

    Never narrowed with `only=`, unlike `aledb_experiment.samples.rebuild_after_structural_change`
    -- a renumber provably cannot change a mutation count, while adding or removing an
    call changes every derived thing an experiment has.

    That used to have a second and sharper reason: aledb-fixation cached MutationCall
    *ids*, in a column only its delete-and-recompute rebuild cleared, and those are rows this
    module hard-deletes. Fixation stores nothing now, so no registered rebuild holds an
    call id and that particular trap is gone. The first reason stands on its own.

    But `request_rebuild` marks the **site-scoped** totals stale too, correctly, and running
    them here made a single delete recount every MutationCall in the installation --
    measured at 4.9 seconds for the read half alone on a 74,859-row database, which is exactly
    the cost `rebuild_after_structural_change` refuses to pay. They stay marked; the dashboard
    calls `ensure_fresh` and rebuilds them on the next view. Ten deletes then cost one recount
    rather than ten.

    That measurement predates `rebuild_mutation_counts` reading tuples rather than
    materialising every call to filter it, so the per-recount price is lower than it
    was. What this does is unchanged: one recount instead of ten is the argument, and it does
    not depend on what one costs.
    """
    from aledb_common.rebuild_registry import (
        EXPERIMENT_SCOPE, request_rebuild, run_rebuilds,
    )

    request_rebuild(experiment.id, reason='mutations edited')
    run_rebuilds(experiment.id, scope=EXPERIMENT_SCOPE)


# --- reconstructing -------------------------------------------------------------------------


def calls_for(experiment, sample_ids=None):
    """Every live call in an experiment, as a queryset.

    Unfiltered on purpose -- no `filter_mutation_calls` here. Filtering is a display
    concern; a mutation excluded by a gene or frequency filter must still be visible to the
    editor, or it cannot be deleted and silently returns when the filter changes.
    """
    queryset = (MutationCall.objects
                .filter(**{_EXPERIMENT_PATH: experiment})
                .select_related("mutation"))
    if sample_ids is not None:
        queryset = queryset.filter(sample_id__in=list(sample_ids))
    return queryset


def live_state(experiment, sample_ids=None):
    """The current calls, as {key: [entry, ...]}."""
    state = {}
    for call in calls_for(experiment, sample_ids):
        entry = _entry(call.sample_id,
                       mutation_identity(call.mutation),
                       call_snapshot(call),
                       mutation=call.mutation,
                       call=call)
        state.setdefault(_entry_key(entry), []).append(entry)
    return state


def state_after(experiment, edit_set=None, sample_ids=None):
    """The calls as they stood immediately after `edit_set` was applied.

    `edit_set=None` means before any recorded change -- what the import produced.

    Derived from the live state by undoing every later edit set, newest first: an ADD is
    undone by dropping an entry with that key, a REMOVE by putting its snapshot back. Walking
    backwards rather than replaying forwards from the import is what makes this cheap in the
    common case, where the point being restored to is recent and the history behind it is long.
    """
    state = live_state(experiment, sample_ids)

    edits = (MutationEdit.objects
               .filter(edit_set__experiment=experiment)
               .select_related("mutation"))
    if edit_set is not None:
        edits = edits.filter(edit_set__pk__gt=edit_set.pk)
    # Newest first, and within an edit set in reverse application order, so each undo sees the
    # state the change it is undoing produced.
    edits = edits.order_by("-edit_set__pk", "-pk")

    wanted = None if sample_ids is None else set(sample_ids)

    for edit in edits:
        if wanted is not None and edit.sample_id not in wanted:
            continue
        entry = _entry(edit.sample_id, edit.mutation_identity, edit.snapshot,
                       mutation=edit.mutation)
        key = _entry_key(entry)
        if edit.operation == OP_ADD:
            bucket = state.get(key)
            if bucket:
                bucket.pop()
                if not bucket:
                    del state[key]
            else:
                logger.warning(
                    "undoing add of %s on sample %s found nothing to remove; the log and the "
                    "rows disagree, most likely because the sample was re-imported",
                    key, edit.sample_id)
        else:
            state.setdefault(key, []).append(entry)

    return state


def plan_restore(experiment, edit_set=None, sample_ids=None):
    """(removals, additions) that would turn the live state into `state_after(edit_set)`.

    Compared per key and by count, so a mutation deleted and later re-copied cancels out and
    produces no change at all -- which is what stops a restore across a long history from
    churning rows that already hold the right value.
    """
    target = state_after(experiment, edit_set, sample_ids)
    current = live_state(experiment, sample_ids)

    removals = []
    additions = []

    for key, entries in current.items():
        surplus = len(entries) - len(target.get(key, ()))
        if surplus > 0:
            removals.extend(entry["call"] for entry in entries[-surplus:])

    for key, entries in target.items():
        missing = len(entries) - len(current.get(key, ()))
        if missing > 0:
            additions.extend(entries[-missing:])

    return removals, additions


def restore(experiment, user, edit_set=None, sample_ids=None, note=""):
    """Put the experiment back to how it stood after `edit_set`, as a *new* edit set.

    Returns the new edit set, or None if the live state already matches -- restoring twice to
    the same point is a no-op the second time rather than a second identical history entry.
    """
    removals, additions = plan_restore(experiment, edit_set, sample_ids)
    if not note:
        note = _restore_note(edit_set, sample_ids)
    edit = apply_edits(experiment, user, KIND_RESTORE,
                           removals=removals, additions=additions,
                           note=note, restored_to=edit_set)
    if edit is not None:
        rebuild_after_edit(experiment)
    return edit


def _restore_note(edit_set, sample_ids):
    where = "the whole experiment"
    if sample_ids:
        where = "%d sample(s)" % len(list(sample_ids))
    if edit_set is None:
        return "Restored %s to the originally imported mutations." % where
    return "Restored %s to the state after change #%d." % (where, edit_set.pk)

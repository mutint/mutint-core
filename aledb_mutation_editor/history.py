"""Applying mutation edits, and reconstructing what the mutations were at any earlier point.

Three ideas, in order of how much they depend on each other.

**A change is an observation appearing or disappearing.** `apply_changes` is the only writer
of `ObservedMutation` in this app, and it writes the log in the same transaction as the rows,
so there is no state in which a row moved and the log did not.

**A version is a changeset.** The rows in a changeset are what that version did. There is no
snapshot table -- the state at a version is *derived*, by taking the live state and undoing
every changeset newer than it, newest first. That is `state_after`. Snapshots would have to be
written on every edit and kept consistent with a log that already says the same thing.

**Restoring is a new change, not a rewind.** `restore` diffs the live state against
`state_after(...)` and applies the difference as a fresh changeset with `kind=RESTORE`. The log
is never rewritten, restoring twice to the same point is a no-op the second time, and a restore
can itself be restored past -- because by the time you look back at it, it is just another
changeset with rows in it.

The identity of an observation, throughout, is `(sample_id, mutation_key, source)` and
deliberately **not** its primary key. A restored row is a new row with a new pk, and the
Mutation it points at may itself have been recreated -- see `mutation_key`. Two observations
can legitimately share that key (nothing in the schema forbids it), so the state maps a key to
a *list* and the diff compares lengths.

Changesets are ordered by **primary key, not by `created_at`**, wherever "newer than" is being
decided. The timestamp is what a person picks a version by and is what the history page shows;
the pk is what guarantees a total order, which two changesets written in the same microsecond
would otherwise not have.
"""

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from aledb_mutation_editor.models import (
    KIND_RESTORE, OP_ADD, OP_REMOVE,
    MutationChange, MutationChangeSet,
)
from aledb_seq.models import Mutation, ObservedMutation

logger = logging.getLogger(__name__)

#: Every column of ObservedMutation except the two foreign keys and the pk. Snapshotted whole
#: so a removal can be undone exactly: a restore that guessed at `frequency` or dropped the
#: read counts would put back a row that renders differently from the one that was deleted.
OBSERVATION_FIELDS = (
    "present", "breseq_present", "gatk_present",
    "wt_reads", "mutated_reads", "other_reads",
    "reference_genome_likelihood", "frequency", "frequency_gatk", "source",
)

#: The fields `gd_import._database_gd_mutations` passes to `Mutation.objects.get_or_create`.
#: Together with the experiment they are a mutation's identity, and they are what lets a
#: swept Mutation row be recreated as the same mutation rather than as a new one.
MUTATION_KEY_FIELDS = (
    "position", "reseq_reference", "mutation_type", "feature_length",
    "sequence_change", "gene",
)

_DECIMAL_FIELDS = ("frequency", "frequency_gatk")

#: The join from an ObservedMutation up to its experiment. Spelled once here; it is the same
#: traversal `aledb_seq.util` and `aledb_filter.util` use.
_EXPERIMENT_PATH = "sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment"


# --- snapshotting ---------------------------------------------------------------------------


def observation_snapshot(observed):
    """Every column of an ObservedMutation, JSON-safe.

    `frequency` and `frequency_gatk` are `DecimalField`s, which JSON cannot carry, so they are
    stored as strings and rebuilt with `Decimal(...)` rather than through `float` -- a round
    trip through float would move the value at the fourth decimal place, which is exactly where
    these columns keep their precision.
    """
    snapshot = {}
    for field in OBSERVATION_FIELDS:
        value = getattr(observed, field)
        if field in _DECIMAL_FIELDS and value is not None:
            value = str(value)
        snapshot[field] = value
    return snapshot


def _observation_kwargs(snapshot):
    """A snapshot back into constructor kwargs."""
    kwargs = {}
    for field in OBSERVATION_FIELDS:
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
    whose last observation this app removed, and a later restore recreates it through
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


def _entry(sample_id, identity, snapshot, mutation=None, observed=None,
           source_sample_id=None):
    """One observation in a state map -- live or reconstructed."""
    return {
        "sample_id": sample_id,
        "identity": identity,
        "observation": snapshot,
        "mutation": mutation,
        "observed": observed,
        "source_sample_id": source_sample_id,
    }


def _entry_key(entry):
    return (entry["sample_id"], key_from_identity(entry["identity"]),
            entry["observation"].get("source"))


# --- applying -------------------------------------------------------------------------------


def _resolve_mutation(experiment, mutation, identity):
    """The Mutation row an addition should point at, recreating it if it has been swept.

    `get_or_create` on exactly the fields `gd_import` uses, so a recreated row is the row a
    re-import would have produced -- and a later import finds it rather than adding a second.
    """
    if mutation is not None and mutation.pk is not None:
        if Mutation.objects.filter(pk=mutation.pk).exists():
            return mutation

    lookup = {field: identity.get(field) for field in MUTATION_KEY_FIELDS}
    recreated, created = Mutation.objects.get_or_create(
        ale_experiment=experiment,
        defaults={
            "gd_data": identity.get("gd_data"),
            "annotation": identity.get("annotation"),
            "product": identity.get("product") or "",
            "protein_change": identity.get("protein_change") or "",
        },
        **lookup)
    if created:
        logger.info("recreated mutation %s for experiment %s from a change-log snapshot",
                    recreated.pk, experiment.ale_id)
    return recreated


@transaction.atomic
def apply_changes(experiment, user, kind, removals=(), additions=(), note="",
                  restored_to=None):
    """Write a changeset and make it true. The only writer of ObservedMutation here.

    removals   ObservedMutation instances to delete.
    additions  entry dicts (see `_entry`): the target sample id, the mutation identity, the
               observation snapshot, and optionally the live Mutation and a source sample.

    Returns the changeset, or None if there was nothing to do. An empty changeset would be a
    history entry recording that nothing happened -- noise on the page, and a step
    `state_after` would walk for no reason.

    The rebuild is deliberately *not* done here; `rebuild_after_edit` runs it outside this
    transaction, for the reason `gd_import` runs its own after committing: a rebuild is long
    and reads what was just written, and holding a write transaction open across it would make
    every concurrent edit queue behind it.
    """
    removals = list(removals)
    additions = list(additions)
    if not removals and not additions:
        return None

    change_set = MutationChangeSet.objects.create(
        ale_experiment=experiment,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        kind=kind,
        note=note,
        restored_to=restored_to)

    changes = []

    for observed in removals:
        changes.append(MutationChange(
            change_set=change_set,
            operation=OP_REMOVE,
            sample_id=observed.sequencing_experiment_id,
            mutation=observed.mutation,
            observation=observation_snapshot(observed),
            mutation_identity=mutation_identity(observed.mutation)))

    for entry in additions:
        identity = entry["identity"]
        resolved = _resolve_mutation(experiment, entry.get("mutation"), identity)
        created = ObservedMutation.objects.create(
            sequencing_experiment_id=entry["sample_id"],
            mutation=resolved,
            **_observation_kwargs(entry["observation"]))
        changes.append(MutationChange(
            change_set=change_set,
            operation=OP_ADD,
            sample_id=entry["sample_id"],
            mutation=resolved,
            source_sample_id=entry.get("source_sample_id"),
            observation=observation_snapshot(created),
            mutation_identity=identity))

    if removals:
        ObservedMutation.objects.filter(
            pk__in=[observed.pk for observed in removals]).delete()

    MutationChange.objects.bulk_create(changes)
    return change_set


def rebuild_after_edit(experiment):
    """Recompute everything an edit invalidated. Call outside the transaction.

    Deliberately unnarrowed, unlike `aledb_experiment.samples.rebuild_after_structural_change`.
    A renumber provably cannot change a mutation count, so that one refuses to pay for the
    dashboard's installation-wide totals. Adding or removing an observation changes every one
    of them -- and aledb-fixation caches ObservedMutation *ids*, which only its
    delete-and-recompute rebuild can clear.
    """
    from aledb_common.rebuild_registry import request_rebuild, run_rebuilds

    request_rebuild(experiment.ale_id, reason='mutations edited')
    run_rebuilds(experiment.ale_id)


# --- reconstructing -------------------------------------------------------------------------


def observations_for(experiment, sample_ids=None):
    """Every live observation in an experiment, as a queryset.

    Unfiltered on purpose -- no `filter_observed_mutations` here. Filtering is a display
    concern; a mutation excluded by a gene or frequency filter must still be visible to the
    editor, or it cannot be deleted and silently returns when the filter changes.
    """
    queryset = (ObservedMutation.objects
                .filter(**{_EXPERIMENT_PATH: experiment})
                .select_related("mutation"))
    if sample_ids is not None:
        queryset = queryset.filter(sequencing_experiment_id__in=list(sample_ids))
    return queryset


def live_state(experiment, sample_ids=None):
    """The current observations, as {key: [entry, ...]}."""
    state = {}
    for observed in observations_for(experiment, sample_ids):
        entry = _entry(observed.sequencing_experiment_id,
                       mutation_identity(observed.mutation),
                       observation_snapshot(observed),
                       mutation=observed.mutation,
                       observed=observed)
        state.setdefault(_entry_key(entry), []).append(entry)
    return state


def state_after(experiment, change_set=None, sample_ids=None):
    """The observations as they stood immediately after `change_set` was applied.

    `change_set=None` means before any recorded change -- what the import produced.

    Derived from the live state by undoing every later changeset, newest first: an ADD is
    undone by dropping an entry with that key, a REMOVE by putting its snapshot back. Walking
    backwards rather than replaying forwards from the import is what makes this cheap in the
    common case, where the point being restored to is recent and the history behind it is long.
    """
    state = live_state(experiment, sample_ids)

    changes = (MutationChange.objects
               .filter(change_set__ale_experiment=experiment)
               .select_related("mutation"))
    if change_set is not None:
        changes = changes.filter(change_set__pk__gt=change_set.pk)
    # Newest first, and within a changeset in reverse application order, so each undo sees the
    # state the change it is undoing produced.
    changes = changes.order_by("-change_set__pk", "-pk")

    wanted = None if sample_ids is None else set(sample_ids)

    for change in changes:
        if wanted is not None and change.sample_id not in wanted:
            continue
        entry = _entry(change.sample_id, change.mutation_identity, change.observation,
                       mutation=change.mutation)
        key = _entry_key(entry)
        if change.operation == OP_ADD:
            bucket = state.get(key)
            if bucket:
                bucket.pop()
                if not bucket:
                    del state[key]
            else:
                logger.warning(
                    "undoing add of %s on sample %s found nothing to remove; the log and the "
                    "rows disagree, most likely because the sample was re-imported",
                    key, change.sample_id)
        else:
            state.setdefault(key, []).append(entry)

    return state


def plan_restore(experiment, change_set=None, sample_ids=None):
    """(removals, additions) that would turn the live state into `state_after(change_set)`.

    Compared per key and by count, so a mutation deleted and later re-copied cancels out and
    produces no change at all -- which is what stops a restore across a long history from
    churning rows that already hold the right value.
    """
    target = state_after(experiment, change_set, sample_ids)
    current = live_state(experiment, sample_ids)

    removals = []
    additions = []

    for key, entries in current.items():
        surplus = len(entries) - len(target.get(key, ()))
        if surplus > 0:
            removals.extend(entry["observed"] for entry in entries[-surplus:])

    for key, entries in target.items():
        missing = len(entries) - len(current.get(key, ()))
        if missing > 0:
            additions.extend(entries[-missing:])

    return removals, additions


def restore(experiment, user, change_set=None, sample_ids=None, note=""):
    """Put the experiment back to how it stood after `change_set`, as a *new* changeset.

    Returns the new changeset, or None if the live state already matches -- restoring twice to
    the same point is a no-op the second time rather than a second identical history entry.
    """
    removals, additions = plan_restore(experiment, change_set, sample_ids)
    if not note:
        note = _restore_note(change_set, sample_ids)
    change = apply_changes(experiment, user, KIND_RESTORE,
                           removals=removals, additions=additions,
                           note=note, restored_to=change_set)
    if change is not None:
        rebuild_after_edit(experiment)
    return change


def _restore_note(change_set, sample_ids):
    where = "the whole experiment"
    if sample_ids:
        where = "%d sample(s)" % len(list(sample_ids))
    if change_set is None:
        return "Restored %s to the originally imported mutations." % where
    return "Restored %s to the state after change #%d." % (where, change_set.pk)

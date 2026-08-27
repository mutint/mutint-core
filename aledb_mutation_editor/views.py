"""Editing a sample's mutations: three pages, three write endpoints.

The shape is the one `aledb_experiment/sample_views.py` established and which everything added
to this codebase since has followed -- a GET page that checks permission itself and renders
`403.html` with a status, plus a `@require_POST` JSON endpoint that checks again, because the
button being hidden is not a permission check.

Two things here differ from the mutation tables next door, and both are deliberate.

**The listings are unfiltered.** `breseq_table` runs its rows through
`aledb_filter.util.filter_observed_mutations`; these pages do not. Filtering is a display
concern, and a mutation excluded by a gene or frequency filter must still be visible to
whoever is curating -- otherwise it cannot be deleted, and it reappears the moment somebody
widens the filter.

**The write endpoints take a list, not one id.** `/ale/experiments/`'s bulk delete fires one
request per row, which is fine for the dozen experiments on that page and wrong for the several
hundred mutations on this one. One POST is also what makes a batch a single changeset, which is
the whole point: "I removed these eleven calls" is one decision and reads as one line of
history.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

import aledb_seq.views.common as seq_common
from aledb_common.logger import user_extra
from aledb_common.util import get_user_context
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import (
    can_add_experiment_filter, experiment_lock_refusal,
)
from aledb_import import annotation
from aledb_mutation_editor import history, record_builder, validation
from aledb_mutation_editor.models import (
    KIND_ADD, KIND_COPY, KIND_DELETE, MutationChangeSet,
)
from aledb_seq.breseq_report import build_rows, is_population
from aledb_seq.models import Mutation, ObservedMutation
from aledb_seq.util import get_reseq_ordered_dict

logger = logging.getLogger(__name__)

REQUEST_RESEQ_ID = "reseq_id"
REQUEST_SOURCE_RESEQ_ID = "source_reseq_id"

#: `?reseq_id=all` -- the whole experiment at once rather than one sample. A sentinel in the
#: same parameter, not a parameter of its own, so the sample picker stays one control with one
#: link per entry and there is no state in which both are set and disagree.
ALL_SAMPLES = "all"

_REFUSED = "You do not have permission to edit this experiment's mutations."


class EditorError(Exception):
    """A refusal the user is meant to read, mirroring `samples.SampleEditError`.

    `errors` maps a field name to the complaint about that field, so the add form can put each
    message beside the input it belongs to. The delete and copy endpoints have one thing to
    say and leave it empty; `message` is always self-sufficient, so a caller with only one
    place to put text loses nothing by ignoring the map.
    """

    def __init__(self, message, status=400, errors=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or {}


# --- shared plumbing ------------------------------------------------------------------------


def _selected_reseq(request, reseq_dict, param=REQUEST_RESEQ_ID):
    """The requested sample, or the experiment's first one."""
    requested = request.GET.get(param)
    if requested:
        try:
            reseq_id = int(requested)
        except (TypeError, ValueError):
            reseq_id = None
        if reseq_id in reseq_dict:
            return reseq_dict[reseq_id]
    for reseq in reseq_dict.values():
        return reseq
    return None


def _rows_for(reseq):
    """One sample's mutations as breseq-style rows, carrying the observation id.

    `build_rows` is aledb_seq's, shared with the Samples page and the genome browser, so the
    editor's table reads identically to the one people already know. It is rendered from this
    app's own template rather than by including `breseq_table/_mutation_table.html`, which is
    shared between those two and must not grow a third caller's checkbox column.
    """
    observed = list(ObservedMutation.objects
                    .filter(sequencing_experiment=reseq)
                    .select_related("mutation"))
    observed.sort(key=lambda o: (o.mutation.reseq_reference or "", o.mutation.position))
    return build_rows(observed)


def _cell_for(observed):
    """One observation as a grid cell.

    `label` is the frequency where there is one, because that is what the read-only tables put
    in the same place, and a tick where there is not -- an observation with no frequency is
    still an assertion that the mutation is there.
    """
    if observed.present is False:
        state, label = "absent", "\u2013"
    elif observed.present:
        state = "present"
        label = ("%.2f" % float(observed.frequency)
                 if observed.frequency is not None else "\u2713")
    else:
        state, label = "unknown", "?"
    return {"id": observed.id, "label": label, "state": state}


#: How many mutations the grid lays out at once. Not a display preference -- a real
#: experiment here is 5,076 mutations across 51 samples, and rendering all of it produced a
#: **32.8 MB** page (measured; the server built it in 1.6s, so the cost is entirely what the
#: browser is then handed). The per-sample mode has no such ceiling because it is one column.
GRID_ROW_LIMIT = 250


def _grid_mutations(experiment, query):
    """The mutations a grid should lay out, narrowed by the search box.

    Narrowing happens here rather than in DataTables because the point is to not *render* the
    rest: a client-side search still ships every row. Position is matched exactly when the
    query is a number, since a substring match on a coordinate is never what anybody means.
    """
    mutations = Mutation.objects.filter(ale_experiment=experiment)
    query = (query or "").strip()
    if query:
        terms = (Q(gene__icontains=query) | Q(reseq_reference__icontains=query)
                 | Q(mutation_type__iexact=query) | Q(sequence_change__icontains=query))
        if query.isdigit():
            terms = terms | Q(position=int(query))
        mutations = mutations.filter(terms)
    return mutations.order_by("reseq_reference", "position", "pk")


def _grid_for(experiment, reseq_dict, query=None):
    """Every observation of the matching mutations, as mutations down and samples across.

    Returns `(rows, by_mutation, by_sample, total, shown)`. The two maps are what the page's
    row and column selectors read: `deferRender` means a cell on an undrawn page has no DOM,
    so "select this whole sample" cannot be done by walking `<td>`s without silently missing
    everything not currently on screen.

    **The maps cover only the rendered rows**, deliberately. They could just as easily cover
    the whole experiment, and then a column selector would put observations into the selection
    that the person cannot see and did not know about -- on a page whose next button deletes
    them.

    Built here rather than through `mutation_table_builder`, for the reasons `_rows_for` gives
    about `breseq_table/_mutation_table.html` and two more of its own. That builder renders a
    cell as an `<a>` into the genome browser, which would fight a click that means "select";
    and `get_table_body` filters through `filter_observed_mutations`, while this page must show
    what is stored -- a mutation hidden by a gene or frequency filter has to stay deletable.
    """
    matching = _grid_mutations(experiment, query)
    total = matching.count()
    page = list(matching[:GRID_ROW_LIMIT])

    column_of = {reseq_id: position for position, reseq_id in enumerate(reseq_dict)}
    rows = {mutation.id: {"mutation": mutation, "cells": [None] * len(column_of)}
            for mutation in page}

    by_mutation = {}
    by_sample = {}
    observed = (history.observations_for(experiment)
                .filter(mutation_id__in=list(rows))
                .order_by("pk"))
    for entry in observed:
        position = column_of.get(entry.sequencing_experiment_id)
        if position is None:
            # A sample the picker is not showing -- `get_reseq_ordered_dict` applies the
            # experiment's sample tag filters. Its observations are not selectable here
            # because they are not on the page; they are untouched, not hidden.
            continue
        rows[entry.mutation_id]["cells"][position] = _cell_for(entry)
        by_mutation.setdefault(entry.mutation_id, []).append(entry.id)
        by_sample.setdefault(entry.sequencing_experiment_id, []).append(entry.id)

    ordered = [rows[mutation.id] for mutation in page]
    # The column headers double as selectors, so each carries how much it would select. A
    # sample with nothing among the rendered rows renders as plain text instead of a link:
    # measured in a browser, an experiment's mutations are often concentrated in a subset of
    # its samples, and a control that looks live and silently does nothing reads as broken.
    columns = [{"reseq": reseq, "count": len(by_sample.get(reseq_id, ()))}
               for reseq_id, reseq in reseq_dict.items()]
    return ordered, columns, by_mutation, by_sample, total, len(ordered)


def _page_context(request, experiment):
    context = get_user_context(request.user)
    context.update(experiment.experiment_context())
    context.update({
        "ale_project_name": experiment.project.name if experiment.project else "",
        "ale_project_id": experiment.project_id,
        "can_edit": can_add_experiment_filter(request.user, experiment),
    })
    return context


class _NotForYou(Exception):
    """The caller may not see this experiment. Carries the response to render instead."""

    def __init__(self, response):
        super().__init__("refused")
        self.response = response


def _experiment_for_page(request, context):
    """The experiment a page is scoped to, or a refusal to render instead.

    `seq_common.get_ale_experiment` signals two different things and neither is an exception
    type of its own: `AleExperiment.DoesNotExist` for "no experiment selected", which is how
    these pages open and is not an error, and a bare `ValueError` for "you may not view this".
    The pages next door catch the second with a blanket `except Exception` and render
    `500.html`, which tells a reader the site broke when in fact they were refused. Here it is
    `403.html` with a 403, matching every other page in this codebase that checks its own
    permission.
    """
    try:
        return seq_common.get_ale_experiment(request)
    except AleExperiment.DoesNotExist:
        raise _NotForYou(seq_common.no_experiment_selected(
            request, context, logger, "mutation editor"))
    except ValueError:
        raise _NotForYou(render(request, "403.html", context, status=403))


def _experiment_for_write(request):
    """The experiment a POST names, refusing if the caller may not edit it.

    Reads `experiment_id` from the POST body rather than the query string: these endpoints are
    reached by `aledbPost`, which sends FormData, and a write that took its scope from the URL
    would be one redirect away from acting on the wrong experiment.
    """
    raw = request.POST.get("experiment_id")
    try:
        experiment = AleExperiment.objects.get(ale_id=raw)
    except (AleExperiment.DoesNotExist, ValueError, TypeError):
        raise EditorError("No such experiment.", status=404)
    if not can_add_experiment_filter(request.user, experiment):
        # "you may not edit this" and "nobody may edit this at the moment" are different
        # answers, and the second one is actionable -- it names the reason and says who can
        # lift it. Falling back to the generic refusal when the lock is not why.
        raise EditorError(experiment_lock_refusal(experiment) or _REFUSED, status=403)
    return experiment


def _int_list(request, field):
    """A JSON array of ids from the POST body.

    `aledbPost` stringifies every FormData value, so a list has to arrive as one JSON string --
    the same convention `experiment_samples_update` uses and for the same reason: flat keys
    need a hand-written parser in which a typo drops a value silently instead of erroring.
    """
    raw = request.POST.get(field) or "[]"
    try:
        values = json.loads(raw)
    except ValueError:
        raise EditorError("Malformed request: %s was not valid JSON." % field)
    if not isinstance(values, list):
        raise EditorError("Malformed request: %s was not a list." % field)
    try:
        return [int(value) for value in values]
    except (TypeError, ValueError):
        raise EditorError("Malformed request: %s held something that is not an id." % field)


def _error_response(error):
    # `errors` rides alongside the summary, which is what `aledbPost` hands the client as
    # `err.body.errors` -- the shape `experiment_samples.html` already reads to outline the
    # rows it was refused on.
    return JsonResponse({"error": error.message, "errors": error.errors},
                        status=error.status)


# --- pages ----------------------------------------------------------------------------------


@ensure_csrf_cookie
def mutation_editor(request):
    """Mutations, selectable, with Delete selected -- one sample or the whole experiment.

    The two modes exist because the questions are different. One sample reads like breseq's
    own report and is where somebody checks a single run; all samples is where the same bad
    call is removed from the twelve samples that carry it, which was twelve page loads before
    even though `mutation_delete` has always taken a list spanning any of them.

    Per-sample stays the default. The grid is the more useful view of a large experiment and
    also the more expensive one, and arriving at a page that has to lay out every mutation
    against every sample is not what somebody following a link from a sample expects.
    """
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.ale_id)
        all_samples = request.GET.get(REQUEST_RESEQ_ID) == ALL_SAMPLES
        reseq = None if all_samples else _selected_reseq(request, reseq_dict)

        context = _page_context(request, experiment)
        context.update({
            "reseq_list": list(reseq_dict.values()),
            "all_samples": all_samples,
            "all_samples_value": ALL_SAMPLES,
            "selected_reseq": reseq,
            "selected_reseq_id": reseq.id if reseq is not None else None,
            "is_population": is_population(reseq),
            "rows": _rows_for(reseq) if reseq is not None else [],
            "recent_changes": _recent_changes(experiment),
            "title": "Edit %s mutations" % experiment.name,
            "template_header": "Edit Mutations",
        })
        if all_samples:
            query = request.GET.get("q", "")
            grid_rows, grid_columns, by_mutation, by_sample, total, shown = _grid_for(
                experiment, reseq_dict, query)
            context.update({
                "grid_rows": grid_rows,
                "grid_columns": grid_columns,
                "by_mutation": by_mutation,
                "by_sample": by_sample,
                "grid_query": query,
                "grid_total": total,
                "grid_shown": shown,
                "grid_truncated": total > shown,
                "grid_limit": GRID_ROW_LIMIT,
            })
        return render(request, "mutation_editor/edit.html", context)
    except _NotForYou as refusal:
        return refusal.response


@ensure_csrf_cookie
def mutation_add(request):
    """Add a mutation nothing in this experiment carries yet."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.ale_id)
        reference_row = _reference_row(experiment)

        context = _page_context(request, experiment)
        context.update({
            "targets": list(reseq_dict.values()),
            "schema": validation.form_schema(),
            # The contigs a position can be on. Offered as a dropdown when they are known,
            # because a mistyped contig name is refused by an exact match with no near-miss
            # handling -- `ReferenceSequences.add` matches names exactly, on purpose.
            "seq_ids": sorted(validation.contig_lengths(reference_row)),
            "has_reference": reference_row is not None,
            "title": "Add a mutation to %s" % experiment.name,
            "template_header": "Add Mutation",
        })
        return render(request, "mutation_editor/add.html", context)
    except _NotForYou as refusal:
        return refusal.response


@ensure_csrf_cookie
def mutation_change(request):
    """Change one mutation's own fields, everywhere in the experiment it is observed.

    Scope is the mutation, not a set of samples. A `Mutation` is experiment-scoped and shared
    by every sample observing it, so correcting a mis-called position is one correction --
    changing it for *some* samples would be splitting one mutation into two, which is
    delete-and-add and stays that way.
    """
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        mutation = _mutation_for_page(request, experiment)

        context = _page_context(request, experiment)
        reference_row = _reference_row(experiment)
        context.update({
            "mutation": mutation,
            "schema": validation.form_schema(),
            "initial": _initial_fields(mutation),
            "sample_count": (history.observations_for(experiment)
                             .filter(mutation=mutation).count()),
            "seq_ids": sorted(validation.contig_lengths(reference_row)),
            "has_reference": reference_row is not None,
            "title": "Change a mutation",
            "template_header": "Change Mutation",
        })
        return render(request, "mutation_editor/change.html", context)
    except _NotForYou as refusal:
        return refusal.response


def _mutation_for_page(request, experiment):
    """The mutation named by `?mutation_id=`, scoped through the experiment.

    Scoped rather than fetched by pk alone, for the reason `mutation_delete` scopes its ids:
    a mutation belongs to one experiment's reference genome, and a hand-typed pk from another
    must not resolve here.
    """
    try:
        return Mutation.objects.get(ale_experiment=experiment,
                                    pk=request.GET.get("mutation_id"))
    except (Mutation.DoesNotExist, ValueError, TypeError):
        raise _NotForYou(render(request, "404.html", get_user_context(request.user),
                                status=404))


def _initial_fields(mutation):
    """What the form opens with: the mutation's stored record, minus its bookkeeping.

    Straight from `gd_data`, which is the verbatim GenomeDiff record -- so the form is
    populated by the same keys `validation.validate_record` will read back, and a field the
    add form knows nothing about cannot appear.
    """
    data = dict(mutation.gd_data or {})
    for key in ("type", "id", "parent_ids", "frequency"):
        data.pop(key, None)
    return {name: ("" if value is None else value) for name, value in data.items()}


def _reference_row(experiment):
    """The experiment's `ExperimentReference`, or None. Reads no files."""
    from aledb_seq.models import ExperimentReference

    return ExperimentReference.objects.filter(ale_experiment=experiment).first()


@ensure_csrf_cookie
def mutation_copy(request):
    """Copy mutations from one sample of this experiment onto others."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.ale_id)
        source = _selected_reseq(request, reseq_dict, REQUEST_SOURCE_RESEQ_ID)

        context = _page_context(request, experiment)
        context.update({
            "reseq_list": list(reseq_dict.values()),
            "source_reseq": source,
            "source_reseq_id": source.id if source is not None else None,
            "targets": [reseq for reseq in reseq_dict.values()
                        if source is None or reseq.id != source.id],
            "rows": _rows_for(source) if source is not None else [],
            "title": "Copy mutations in %s" % experiment.name,
            "template_header": "Copy Mutations",
        })
        return render(request, "mutation_editor/copy.html", context)
    except _NotForYou as refusal:
        return refusal.response


def mutation_history(request):
    """Every recorded change to this experiment's mutations, newest first."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        context = _page_context(request, experiment)
        context.update({
            "changesets": _changesets(experiment),
            "title": "Mutation history for %s" % experiment.name,
            "template_header": "Mutation History",
        })
        return render(request, "mutation_editor/history.html", context)
    except _NotForYou as refusal:
        return refusal.response


def _changesets(experiment, limit=None):
    queryset = (MutationChangeSet.objects
                .filter(ale_experiment=experiment)
                .select_related("created_by")
                .prefetch_related("changes__sample__tech_rep__isolate__flask__ale_id"))
    if limit is not None:
        queryset = queryset[:limit]
    return [_changeset_context(change_set) for change_set in queryset]


def _recent_changes(experiment):
    return _changesets(experiment, limit=5)


def _changeset_context(change_set):
    """One history row: who, when, and a per-sample tally rather than every row.

    A batch copy across twenty samples is hundreds of MutationChange rows, and a page that
    listed each of them would bury the one thing a person is looking for -- which samples moved
    and by how much. The rows themselves are still there to expand into.
    """
    added = removed = 0
    samples = {}
    for change in change_set.changes.all():
        label = change.sample.ale_flask_isolate_str if change.sample_id else "(deleted sample)"
        tally = samples.setdefault(label, {"label": label, "added": 0, "removed": 0})
        if change.operation == "add":
            added += 1
            tally["added"] += 1
        else:
            removed += 1
            tally["removed"] += 1
    return {
        "change_set": change_set,
        "added": added,
        "removed": removed,
        "samples": sorted(samples.values(), key=lambda entry: entry["label"]),
    }


# --- writes ---------------------------------------------------------------------------------


@require_POST
def mutation_delete(request):
    """Remove selected observations from one sample."""
    try:
        experiment = _experiment_for_write(request)
        observed_ids = _int_list(request, "observed_ids")
        if not observed_ids:
            raise EditorError("Select at least one mutation to delete.")

        # Scoped through the experiment, not taken on trust: an id from another experiment
        # simply does not match, so a hand-built POST cannot reach across projects.
        removals = list(history.observations_for(experiment)
                        .filter(pk__in=observed_ids))
        if not removals:
            raise EditorError("Those mutations are not in this experiment.", status=404)

        change_set = history.apply_changes(
            experiment, request.user, KIND_DELETE, removals=removals,
            note="Deleted %d mutation(s)." % len(removals))
    except EditorError as error:
        return _error_response(error)

    history.rebuild_after_edit(experiment)
    logger.info("mutations deleted", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "removed": len(removals),
                         "change_set_id": change_set.pk if change_set else None})


@require_POST
def mutation_copy_apply(request):
    """Copy chosen mutations from one sample onto one or more others.

    A target that already carries the mutation is skipped rather than given a second
    observation of it: "make sure this call is on these samples too" is what the button means,
    and duplicating a row would quietly double that sample's count of it.
    """
    try:
        experiment = _experiment_for_write(request)
        mutation_ids = _int_list(request, "mutation_ids")
        target_ids = _int_list(request, "target_reseq_ids")
        if not mutation_ids:
            raise EditorError("Select at least one mutation to copy.")
        if not target_ids:
            raise EditorError("Select at least one sample to copy to.")

        source_id = request.POST.get(REQUEST_SOURCE_RESEQ_ID)
        sources = list(history.observations_for(experiment)
                       .filter(sequencing_experiment_id=source_id,
                               mutation_id__in=mutation_ids))
        if not sources:
            raise EditorError("Those mutations are not in the source sample.", status=404)

        targets = {reseq.id: reseq for reseq in
                   get_reseq_ordered_dict(experiment.ale_id).values()
                   if reseq.id in set(target_ids)}
        if not targets:
            raise EditorError("Those samples are not in this experiment.", status=404)

        additions, skipped = _plan_copy(experiment, sources, targets)
        change_set = history.apply_changes(
            experiment, request.user, KIND_COPY, additions=additions,
            note="Copied %d mutation(s) to %d sample(s)." % (len(sources), len(targets)))
    except EditorError as error:
        return _error_response(error)

    if change_set is not None:
        history.rebuild_after_edit(experiment)
    logger.info("mutations copied", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "added": len(additions),
                         "skipped": skipped,
                         "change_set_id": change_set.pk if change_set else None})


def _plan_copy(experiment, sources, targets):
    """Additions for copying each source observation onto each target that lacks it."""
    existing = history.live_state(experiment, sample_ids=list(targets))
    additions = []
    skipped = 0
    for observed in sources:
        identity = history.mutation_identity(observed.mutation)
        snapshot = history.observation_snapshot(observed)
        for target_id in targets:
            key = (target_id, history.key_from_identity(identity), snapshot.get("source"))
            if existing.get(key):
                skipped += 1
                continue
            additions.append({
                "sample_id": target_id,
                "identity": identity,
                "observation": snapshot,
                "mutation": observed.mutation,
                "observed": None,
                "source_sample_id": observed.sequencing_experiment_id,
            })
            # Keep the map current so copying two source rows that collapse to the same key
            # onto one target adds it once rather than twice.
            existing.setdefault(key, []).append(None)
    return additions, skipped


@require_POST
def mutation_add_apply(request):
    """Create one mutation and observe it in every selected sample.

    The mutation itself is minted by `history.apply_changes` through `_resolve_mutation`, which
    `get_or_create`s on the same seven fields the importer keys on -- so adding a call another
    sample already carries links the existing row instead of forking it.
    """
    try:
        experiment = _experiment_for_write(request)
        target_ids = _int_list(request, "target_reseq_ids")
        if not target_ids:
            raise EditorError("Select at least one sample to add it to.")

        mutation_type = (request.POST.get("mutation_type") or "").strip().upper()
        frequency = _frequency(request)

        reference_row = _reference_row(experiment)
        attributes, errors = validation.validate_record(
            request.POST, mutation_type,
            reference_row=reference_row,
            # A callable, not a value: loading parses the whole genome, and most types are
            # decided without ever reading a base.
            load_references=lambda: annotation.reference_sequences_for(experiment))
        if errors:
            raise EditorError("That mutation cannot be added as entered.", errors=errors)

        targets = {reseq.id: reseq for reseq in
                   get_reseq_ordered_dict(experiment.ale_id).values()
                   if reseq.id in set(target_ids)}
        if not targets:
            raise EditorError("Those samples are not in this experiment.", status=404)

        gd_data = record_builder.build_gd_data(mutation_type, attributes)
        annotated_record, _ = record_builder.annotate(gd_data, experiment)
        identity = record_builder.build_identity(mutation_type, gd_data, annotated_record)
        observation = record_builder.build_observation(frequency)

        additions, skipped = _plan_add(experiment, identity, observation, targets)
        change_set = history.apply_changes(
            experiment, request.user, KIND_ADD, additions=additions,
            note="Added %s at %s:%s to %d sample(s)." % (
                mutation_type, attributes.get("seq_id"), attributes.get("position"),
                len(additions)))
    except EditorError as error:
        return _error_response(error)

    if change_set is not None:
        # The promoted annotation columns are not part of the identity, so the row `apply_changes`
        # minted has them null until this runs -- and would render through the unannotated
        # fallback. Same call `gd_import` makes after its own get_or_create.
        record_builder.apply_annotation(change_set.changes.first().mutation, annotated_record)
        history.rebuild_after_edit(experiment)

    logger.info("mutation added", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "added": len(additions),
                         "skipped": skipped,
                         "change_set_id": change_set.pk if change_set else None})


def _frequency(request):
    """The one frequency every selected sample's observation gets."""
    raw = (request.POST.get("frequency") or "").strip()
    if not raw:
        return Decimal("1.0")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise EditorError("That mutation cannot be added as entered.",
                          errors={"frequency": "Must be a number between 0 and 1."})
    if not Decimal("0") < value <= Decimal("1"):
        raise EditorError("That mutation cannot be added as entered.",
                          errors={"frequency": "Frequencies run from just above 0 to 1."})
    return value


def _plan_add(experiment, identity, observation, targets):
    """One addition per target that does not already carry this mutation."""
    existing = history.live_state(experiment, sample_ids=list(targets))
    key_part = history.key_from_identity(identity)

    additions = []
    skipped = 0
    for target_id in targets:
        if existing.get((target_id, key_part, observation.get("source"))):
            skipped += 1
            continue
        additions.append({
            "sample_id": target_id,
            "identity": identity,
            "observation": observation,
            "mutation": None,          # -- minted by _resolve_mutation from the identity
            "observed": None,
            "source_sample_id": None,  # -- nothing was copied; this is a new assertion
        })
    return additions, skipped


@require_POST
def mutation_change_apply(request):
    """Move one mutation to a new set of field values, taking its observations with it."""
    try:
        experiment = _experiment_for_write(request)
        mutation = _mutation_for_write(request, experiment)

        mutation_type = (request.POST.get("mutation_type") or "").strip().upper()
        reference_row = _reference_row(experiment)
        attributes, errors = validation.validate_record(
            request.POST, mutation_type,
            reference_row=reference_row,
            load_references=lambda: annotation.reference_sequences_for(experiment))
        if errors:
            raise EditorError("That mutation cannot be changed as entered.", errors=errors)

        gd_data = record_builder.build_gd_data(mutation_type, attributes)
        annotated_record, _ = record_builder.annotate(gd_data, experiment)
        identity = record_builder.build_identity(mutation_type, gd_data, annotated_record)

        _refuse_unchanged(mutation, identity)
        _refuse_collision(experiment, mutation, identity)

        change_set = history.apply_mutation_edit(
            experiment, request.user, mutation, identity,
            note="Changed %s at %s:%s to %s at %s:%s." % (
                mutation.mutation_type, mutation.reseq_reference, mutation.position,
                mutation_type, attributes.get("seq_id"), attributes.get("position")))
        if change_set is None:
            raise EditorError("No sample carries that mutation, so there is nothing to "
                              "change.", status=404)
    except EditorError as error:
        return _error_response(error)

    # The promoted annotation columns are not part of the identity, so `apply_mutation_edit`
    # left them describing the mutation as it was. Same call the add path makes.
    record_builder.apply_annotation(mutation, annotated_record)
    history.rebuild_after_edit(experiment)
    logger.info("mutation changed", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "mutation_id": mutation.pk,
                         "samples": change_set.changes.filter(operation="add").count(),
                         "change_set_id": change_set.pk})


def _mutation_for_write(request, experiment):
    try:
        return Mutation.objects.get(ale_experiment=experiment,
                                    pk=request.POST.get("mutation_id"))
    except (Mutation.DoesNotExist, ValueError, TypeError):
        raise EditorError("That mutation is not in this experiment.", status=404)


def _refuse_unchanged(mutation, identity):
    """A change that changes nothing is refused rather than logged.

    Both halves matter: the six key fields decide what the mutation *is*, and `gd_data` can
    move without them -- a MOB's `strand`, say -- which is a real change to what
    `to_gd_line()` writes even though the identity is the same.
    """
    if (history.mutation_key(mutation) == history.key_from_identity(identity)
            and (mutation.gd_data or None) == (identity.get("gd_data") or None)):
        raise EditorError("Those are the values it already has.")


def _refuse_collision(experiment, mutation, identity):
    """Refuse an edit that would duplicate another mutation in the same experiment.

    The six-field key is what `gd_import` dedups on, so two rows sharing it is a state the
    importer cannot produce and would resolve arbitrarily if it met one. An edit is the only
    way to reach it. Merging the two instead would silently destroy a row, and the person may
    not have realised they were the same.
    """
    key = {field: identity.get(field) for field in history.MUTATION_KEY_FIELDS}
    clash = (Mutation.objects.filter(ale_experiment=experiment, **key)
             .exclude(pk=mutation.pk).first())
    if clash is not None:
        raise EditorError(
            "That would make this identical to mutation %d (%s %s %s), which this experiment "
            "already has." % (clash.pk, clash.reseq_reference or "", clash.position,
                              clash.mutation_type))


@require_POST
def mutation_restore(request):
    """Put the experiment, or chosen samples of it, back to an earlier point."""
    try:
        experiment = _experiment_for_write(request)

        raw = (request.POST.get("change_set_id") or "").strip()
        change_set = None
        if raw:
            change_set = MutationChangeSet.objects.filter(
                pk=raw, ale_experiment=experiment).first()
            if change_set is None:
                raise EditorError("That point is not in this experiment's history.",
                                  status=404)

        sample_ids = _int_list(request, "reseq_ids") or None
        change = history.restore(experiment, request.user, change_set, sample_ids)
    except EditorError as error:
        return _error_response(error)

    logger.info("mutations restored", extra=user_extra(request))
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "change_set_id": change.pk if change else None,
                         "changed": change is not None})

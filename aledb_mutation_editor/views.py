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

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

import aledb_seq.views.common as seq_common
from aledb_common.logger import user_extra
from aledb_common.util import get_user_context
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_add_experiment_filter
from aledb_mutation_editor import history
from aledb_mutation_editor.models import KIND_COPY, KIND_DELETE, MutationChangeSet
from aledb_seq.breseq_report import build_rows, is_population
from aledb_seq.models import ObservedMutation
from aledb_seq.util import get_reseq_ordered_dict

logger = logging.getLogger(__name__)

REQUEST_RESEQ_ID = "reseq_id"
REQUEST_SOURCE_RESEQ_ID = "source_reseq_id"

_REFUSED = "You do not have permission to edit this experiment's mutations."


class EditorError(Exception):
    """A refusal the user is meant to read, mirroring `samples.SampleEditError`."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


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
        raise EditorError(_REFUSED, status=403)
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
    return JsonResponse({"error": error.message}, status=error.status)


# --- pages ----------------------------------------------------------------------------------


@ensure_csrf_cookie
def mutation_editor(request):
    """One sample's mutations, selectable, with Delete selected."""
    context = get_user_context(request.user)
    try:
        experiment = _experiment_for_page(request, context)
        reseq_dict = get_reseq_ordered_dict(experiment.ale_id)
        reseq = _selected_reseq(request, reseq_dict)

        context = _page_context(request, experiment)
        context.update({
            "reseq_list": list(reseq_dict.values()),
            "selected_reseq": reseq,
            "selected_reseq_id": reseq.id if reseq is not None else None,
            "is_population": is_population(reseq),
            "rows": _rows_for(reseq) if reseq is not None else [],
            "recent_changes": _recent_changes(experiment),
            "title": "Edit %s mutations" % experiment.name,
            "template_header": "Edit Mutations",
        })
        return render(request, "mutation_editor/edit.html", context)
    except _NotForYou as refusal:
        return refusal.response


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

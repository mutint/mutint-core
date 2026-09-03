"""Editing samples: one page per sample, and one editable table per experiment.

Both shapes exist because they answer different questions. You reach the per-sample page
from the sample you are looking at -- the mutations table, the runs list -- having just
noticed one thing is wrong. You reach the table when a whole experiment was imported under
the wrong numbering and twenty samples need moving at once.

They share `aledb_experiment.samples.apply_rows`, so the single-sample path is literally
the bulk path with a one-element list. Validation, collision rules and orphan pruning
cannot drift between them, which they would have within a release if each had its own.

The split between a GET page and a POST endpoint is the one `project_new`/`project_create`
already uses: the page renders and checks permission because it has a URL people will
reach directly, and the endpoint checks again because that is where the write happens.
"""

import json
import logging

from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from aledb_common.util import get_user_context
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_edit_experiment
from aledb_experiment.samples import (
    SampleEditError, apply_rows, coordinate_str, parse_rows, plan_moves,
    rebuild_after_structural_change, rows_are_structural, sample_coordinate,
    sample_experiment, sample_project,
)

logger = logging.getLogger(__name__)


def _experiment_samples(experiment):
    """Every sample of an experiment, in A/F/I/R order.

    Imported here rather than at module scope: aledb_experiment must not import aledb_seq
    at load time -- the dependency runs the other way, which is why
    `ResequencingExperiment.tech_rep` names its target by string.
    """
    from aledb_seq.util import get_ordered_reseq_queryset
    # `include_ancestor=True`: this is the page that edits and deletes samples, so it has to
    # show the designated ancestor, which every reading page hides.
    return get_ordered_reseq_queryset(experiment.ale_id, include_ancestor=True)


def _get_sample(pk):
    """The sample and its experiment, or a 404.

    A sample with no `tech_rep` has no project and therefore nothing to check permission
    against. Rather than let this page become the one route into the database that skips
    the check, those 404: they are filtered out of every list already, and repairing them
    belongs in a management command.
    """
    from aledb_seq.models import ResequencingExperiment
    from django.http import Http404

    reseq = get_object_or_404(ResequencingExperiment, pk=pk)
    experiment = sample_experiment(reseq)
    if experiment is None:
        raise Http404("This sample is not attached to an experiment.")
    return reseq, experiment


def _row_context(reseq):
    """What both templates need per sample.

    `label` is deliberately alongside `coordinate`. `ale_flask_isolate_str` returns
    `Isolate.description` verbatim whenever it is set, and the import path sets it to the
    filename for every sample whose name is not already an A-F-I-R string -- so on a
    typical experiment the coordinate can change and every label on every page stays
    byte-identical. Showing both, next to an editable description, is what stops a
    successful save looking like it did nothing.
    """
    coordinate = sample_coordinate(reseq)
    tech_rep = reseq.tech_rep
    isolate = tech_rep.isolate
    return {
        "reseq": reseq,
        "id": reseq.pk,
        "coordinate": coordinate_str(coordinate),
        "label": reseq.ale_flask_isolate_str,
        "ale": coordinate[0],
        "flask": coordinate[1],
        "isolate": coordinate[2],
        "rep": coordinate[3],
        "is_population": isolate.is_population,
        "isolate_description": isolate.description or "",
        "rep_description": tech_rep.description or "",
        "rep_tags": tech_rep.tags or "",
    }


# --- pages ---------------------------------------------------------------------------------


@ensure_csrf_cookie
def sample_edit(request, pk):
    """One sample's identity and description."""
    reseq, experiment = _get_sample(pk)
    context = get_user_context(request.user)
    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", context, status=403)

    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "sample": _row_context(reseq),
    })
    return render(request, "ale/sample_edit.html", context)


@ensure_csrf_cookie
def experiment_samples(request, pk):
    """Every sample of one experiment, editable in a single save."""
    experiment = get_object_or_404(AleExperiment, pk=pk)
    context = get_user_context(request.user)
    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", context, status=403)

    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "samples": [_row_context(reseq) for reseq in _experiment_samples(experiment)],
    })
    return render(request, "ale/experiment_samples.html", context)


# --- endpoints -----------------------------------------------------------------------------


def _save(experiment, rows):
    """Validate, plan, apply -- and rebuild only if identity actually moved.

    Three phases rather than one loop so that a bad row costs nothing: phases one and two
    touch no rows at all, which is what makes "nothing was saved" true rather than
    aspirational.
    """
    samples_by_id = {str(reseq.pk): reseq for reseq in _experiment_samples(experiment)}

    parsed = parse_rows(rows, samples_by_id)
    parsed = plan_moves(parsed, samples_by_id)
    structural = rows_are_structural(parsed)

    from aledb_import.gd_import import prepare_experiment_by_id
    # Once per request, not once per row: it get_or_creates the placeholder Media, and it
    # is only ever a fallback for a sample with nothing to inherit.
    placeholders = prepare_experiment_by_id(experiment.ale_id)

    try:
        touched = apply_rows(experiment, parsed, media=placeholders["media"])
    except IntegrityError:
        logger.warning("concurrent edit on experiment %s", experiment.ale_id)
        raise SampleEditError(
            "Another edit changed this sample while you were working. "
            "Reload the page and try again.", status=409)

    if structural:
        rebuild_after_structural_change(experiment)
    return touched, structural


def _error_response(error):
    return JsonResponse({"error": error.message, "errors": error.errors},
                        status=error.status)


@require_POST
def sample_update(request, pk):
    reseq, experiment = _get_sample(pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot edit this sample."}, status=403)

    row = {
        "id": str(reseq.pk),
        "sample_name": request.POST.get("sample_name"),
        # No "person": samples.apply_rows only writes the keys it is handed, so leaving it
        # out means no edit page can touch it. Changing who owns or ran something is its
        # own workflow, not a side effect of correcting a sample's numbering.
        "ale": request.POST.get("ale"),
        "flask": request.POST.get("flask"),
        "isolate": request.POST.get("isolate"),
        "rep": request.POST.get("rep"),
        # Passed through raw: samples._truthy decides, because an unchecked box posts
        # the string "0", which bool() reads as True.
        "is_population": request.POST.get("is_population"),
        "isolate_description": request.POST.get("isolate_description"),
        "rep_description": request.POST.get("rep_description"),
        "rep_tags": request.POST.get("rep_tags"),
    }
    try:
        _save(experiment, [row])
    except SampleEditError as error:
        return _error_response(error)

    reseq.refresh_from_db()
    return JsonResponse({"sample_id": reseq.pk,
                         "experiment_id": experiment.ale_id,
                         "coordinate": coordinate_str(sample_coordinate(reseq))})


@require_POST
def experiment_samples_update(request, pk):
    """Save the whole table at once.

    The payload is one JSON string under `rows`. `aledbPost` builds FormData with
    `form.append(k, data[k])`, so a nested object would arrive as "[object Object]" -- a
    JSON string is the one nested shape that survives it. The alternative, flat keys like
    `sb-ale-37`, needs a hand-written key parser in which a typo drops a field silently
    instead of erroring.
    """
    experiment = get_object_or_404(AleExperiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot edit this experiment's samples."},
                            status=403)

    try:
        rows = json.loads(request.POST.get("rows") or "[]")
    except ValueError:
        return JsonResponse({"error": "Malformed request: rows was not valid JSON."},
                            status=400)

    try:
        touched, structural = _save(experiment, rows)
    except SampleEditError as error:
        return _error_response(error)

    return JsonResponse({"experiment_id": experiment.ale_id,
                         "updated": touched,
                         "structural": structural})

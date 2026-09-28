"""Editing samples: one page per sample, and one editable table per experiment.

Both shapes exist because they answer different questions. You reach the per-sample page
from the sample you are looking at -- the mutations table, the runs list -- having just
noticed one thing is wrong. You reach the table when a whole experiment was imported under
the wrong numbering and twenty samples need moving at once.

They share `mutint_experiment.samples.apply_rows`, so the single-sample path is literally
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

from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment
from mutint_experiment.samples import (
    SampleEditError, apply_rows, coordinate_str, parse_rows, plan_moves,
    rebuild_after_structural_change, rows_are_structural, sample_coordinate,
    sample_experiment, sample_project,
)
from mutint_experiment.coordinates import format_time_point
from mutint_sample.flags import FLAG_FIELDS, FLAGS

logger = logging.getLogger(__name__)


def _experiment_samples(experiment):
    """Every sample of an experiment, in A/F/I/R order.

    Imported here rather than at module scope: mutint_experiment must not import mutint_sample
    at load time -- the dependency runs the other way, which is why
    `Sample.time_point` names its target by string.
    """
    from mutint_sample.util import get_ordered_sample_queryset
    # `include_ancestor=True`: this is the page that edits and deletes samples, so it has to
    # show the designated ancestor, which every reading page hides.
    return get_ordered_sample_queryset(experiment.id, include_ancestor=True)


def _get_sample(pk):
    """The sample and its experiment, or a 404.

    A sample with no `flask` has no project and therefore nothing to check permission
    against. Rather than let this page become the one route into the database that skips
    the check, those 404: they are filtered out of every list already, and repairing them
    belongs in a management command.
    """
    from mutint_sample.models import Sample
    from django.http import Http404

    sample = get_object_or_404(Sample, pk=pk)
    experiment = sample_experiment(sample)
    if experiment is None:
        raise Http404("This sample is not attached to an experiment.")
    return sample, experiment


def _row_context(sample):
    """What both templates need per sample.

    `label` is deliberately alongside `coordinate`. `label` returns
    the sample's `description` verbatim whenever it is set, and the import path sets it to the
    filename for every sample whose name is not already an A-F-I-R string -- so on a
    typical experiment the coordinate can change and every label on every page stays
    byte-identical. Showing both, next to an editable description, is what stops a
    successful save looking like it did nothing.
    """
    coordinate = sample_coordinate(sample)
    return {
        "sample": sample,
        "id": sample.pk,
        "source_name": sample.source_name,
        "coordinate": coordinate_str(coordinate),
        "label": sample.label,
        "population": coordinate[0],
        # Through the formatter, so an integral float shows as `500` in the box and not
        # `500.0` -- see `samples._optional_time_point` for what the latter used to do on
        # save. None renders as an empty box, which is a sample nobody has placed.
        "time_point": format_time_point(coordinate[1]),
        "name": coordinate[2],
        "is_mixed": sample.is_mixed,
        "description": sample.description or "",
        "treatment": sample.treatment or "",
        "medium_description": sample.curation.get("medium_description") or "",
        # The flags as the templates draw them: what to call each, and whether it is on.
        "flags": [{"field": flag.field, "key": flag.key, "label": flag.label,
                   "help": flag.help, "on": bool(getattr(sample, flag.field))}
                  for flag in FLAGS],
    }


# --- pages ---------------------------------------------------------------------------------


@ensure_csrf_cookie
def sample_edit(request, pk):
    """One sample's identity and description."""
    sample, experiment = _get_sample(pk)
    context = get_user_context(request.user)
    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", context, status=403)

    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "sample": _row_context(sample),
        "treatment_names": _treatment_names(experiment),
    })
    return render(request, "sample/edit.html", context)


def _treatment_names(experiment):
    """The treatments this experiment's samples carry, for the boxes' suggestion list.

    The experiment's list of treatments *is* its samples' distinct values (see
    `Sample.treatment`), so a page that edits one offers the others back rather than a
    vocabulary somebody has to maintain first. Asked with the ancestor included, since this
    page shows it.
    """
    from mutint_sample.views.common import get_treatment_names

    return get_treatment_names(experiment.pk, include_ancestor=True)


@ensure_csrf_cookie
def experiment_samples(request, pk):
    """Every sample of one experiment, editable in a single save."""
    experiment = get_object_or_404(Experiment, pk=pk)
    context = get_user_context(request.user)
    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", context, status=403)

    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "samples": [_row_context(sample) for sample in _experiment_samples(experiment)],
        # For the table's header row; each sample row carries its own resolved copy.
        "flags": FLAGS,
        "treatment_names": _treatment_names(experiment),
    })
    return render(request, "sample/list.html", context)


def experiment_samples_selected(request):
    """The sidebar's **Samples** entry: `?experiment_id=` to the page's own address.

    Every experiment-section link is its url with the experiment appended as a query
    parameter, and this page names the experiment in its path. A redirect rather than a
    second rendering keeps one address for the page. Permission is the page's to check.
    """
    from django.http import Http404
    from django.shortcuts import redirect

    raw = request.GET.get("experiment_id", "")
    if not raw.isdigit():
        raise Http404("No experiment selected.")
    experiment = get_object_or_404(Experiment, pk=int(raw))
    return redirect("experiment_samples", pk=experiment.pk)


# --- the spreadsheet -----------------------------------------------------------------------
#
# The Edit samples page's table as the import's own `metadata.csv`, and that file read back
# into the table. **Reading it writes nothing**: the values go into the page's boxes, the
# person sees what changed, and Save posts them through `_save` like anything typed. One
# validation path, and an upload that is wrong costs a reload rather than a restore.

#: A spreadsheet of samples, not a genome: a few hundred rows is tens of kilobytes.
MAX_METADATA_BYTES = 2 * 1024 * 1024


def _data_name(sample):
    """What the spreadsheet's `data` column calls a sample -- the name import matches on."""
    return sample.source_name or "sample_%d" % sample.pk


def experiment_samples_metadata(request, pk):
    """The experiment's samples as a `metadata.csv`, filled in with what is stored now."""
    from django.http import HttpResponse
    from mutint_import import metadata
    from mutint_import.archive import slug

    experiment = get_object_or_404(Experiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        return render(request, "403.html", get_user_context(request.user), status=403)

    entries = []
    for sample in _experiment_samples(experiment):
        coordinate = sample_coordinate(sample)
        entries.append({
            "sample": coordinate[2],
            "population": coordinate[0],
            "time_point": coordinate[1],
            "is_clonal": not sample.is_mixed,
            "treatment": sample.treatment or "",
            "description": sample.description or "",
            "flags": {field: bool(getattr(sample, field)) for field in FLAG_FIELDS},
            "data": _data_name(sample),
        })
    response = HttpResponse(metadata.write(entries), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="%s_metadata.csv"' % slug(experiment.name or "experiment"))
    return response


@require_POST
def experiment_samples_read_metadata(request, pk):
    """Read an uploaded `metadata.csv` into values for the table's boxes; writes nothing.

    Each sample is found by its `data` name, through `Metadata.lookup` -- the import's own
    matching, extension optional. The answer carries only what a row said: a blank flag
    cell leaves that checkbox alone, and a row with no time point sends a blank one and no
    population, so the population box keeps what it had and Save still asks for one.
    """
    from mutint_import import metadata

    experiment = get_object_or_404(Experiment, pk=pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot edit this experiment's samples."},
                            status=403)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "Choose a metadata.csv to upload."}, status=400)
    if upload.size > MAX_METADATA_BYTES:
        return JsonResponse({"error": "%s is too large to be a sample spreadsheet."
                             % upload.name}, status=400)

    try:
        parsed = metadata.parse(upload.read(), source=upload.name)
        samples = {}
        unnamed = []
        for sample in _experiment_samples(experiment):
            row = parsed.lookup(_data_name(sample))
            if row is None:
                unnamed.append(sample.label)
                continue
            values = {"name": row.sample,
                      "time_point": format_time_point(row.time_point),
                      "flags": dict(row.flags)}
            if row.population:
                values["population"] = row.population
            if row.is_clonal is not None:
                values["is_mixed"] = not row.is_clonal
            if row.description is not None:
                values["description"] = row.description
            if row.treatment is not None:
                values["treatment"] = row.treatment
            samples[str(sample.pk)] = values
    except metadata.MetadataError as error:
        return JsonResponse({"error": str(error)}, status=400)

    return JsonResponse({"samples": samples,
                         "unmatched_rows": parsed.report()["unmatched_rows"],
                         "unnamed_samples": unnamed})


# --- endpoints -----------------------------------------------------------------------------


def _save(experiment, rows):
    """Validate, plan, apply -- and rebuild only if identity actually moved.

    Three phases rather than one loop so that a bad row costs nothing: phases one and two
    touch no rows at all, which is what makes "nothing was saved" true rather than
    aspirational.
    """
    samples_by_id = {str(sample.pk): sample for sample in _experiment_samples(experiment)}

    parsed = parse_rows(rows, samples_by_id)
    parsed = plan_moves(parsed, samples_by_id)
    structural = rows_are_structural(parsed)

    try:
        touched = apply_rows(experiment, parsed)
    except IntegrityError:
        logger.warning("concurrent edit on experiment %s", experiment.id)
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
    sample, experiment = _get_sample(pk)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot edit this sample."}, status=403)

    row = {
        "id": str(sample.pk),
        "source_name": request.POST.get("source_name"),
        "population": request.POST.get("population"),
        "time_point": request.POST.get("time_point"),
        "name": request.POST.get("name"),
        "rep": request.POST.get("rep"),
        # Passed through raw: samples._truthy decides, because an unchecked box posts
        # the string "0", which bool() reads as True.
        "is_mixed": request.POST.get("is_mixed"),
        "description": request.POST.get("description"),
        "medium_description": request.POST.get("medium_description"),
    }
    # Only the flags the form sent: an absent key leaves the flag alone, so a client that
    # predates one of them cannot clear it by not knowing about it. The treatment is the
    # same: it arrived after the page had users.
    for field in FLAG_FIELDS + ("treatment",):
        if field in request.POST:
            row[field] = request.POST.get(field)
    try:
        _save(experiment, [row])
    except SampleEditError as error:
        return _error_response(error)

    sample.refresh_from_db()
    return JsonResponse({"sample_id": sample.pk,
                         "experiment_id": experiment.id,
                         "coordinate": coordinate_str(sample_coordinate(sample))})


@require_POST
def experiment_samples_update(request, pk):
    """Save the whole table at once.

    The payload is one JSON string under `rows`. `mutintPost` builds FormData with
    `form.append(k, data[k])`, so a nested object would arrive as "[object Object]" -- a
    JSON string is the one nested shape that survives it. The alternative, flat keys like
    `sb-population-37`, needs a hand-written key parser in which a typo drops a field silently
    instead of erroring.
    """
    experiment = get_object_or_404(Experiment, pk=pk)
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

    return JsonResponse({"experiment_id": experiment.id,
                         "updated": touched,
                         "structural": structural})

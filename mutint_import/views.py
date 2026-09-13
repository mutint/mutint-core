"""What is left of the standalone import views: the .gd and .vcf export downloads.

`gd_import_view` (`/import/`) and `reference_views.reference_upload_view`
(`/import/reference/`) lived here until they were retired. Both were unlinked, both went
through the name-based `_prepare_experiment`, which cannot reach the second of two
experiments sharing a name, and neither had any authorization. The Add page (`add_views.py`) plus the chunked session endpoints replaced
them; reference *annotation* replacement, the one capability only the reference page had,
is now the `replace_annotation` import type in `handlers.py`.
"""

from django.http import HttpResponse
from django.utils.http import content_disposition_header

from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_view_project
from mutint_import import archive, gd_import, vcf_export
from mutint_sample.models import Sample


def _viewable_sample(request, sample_id):
    """The sample, or an HttpResponse refusing it.

    **`can_view_project`, which the .gd export did not ask.** It had no check of any kind, so
    a `.gd` for any sample id was downloadable by anyone -- and with `LoginRequiredMiddleware`
    installed only in `settings_private.py`, "anyone" included an anonymous caller. Fixed
    here rather than left alone because the VCF export sits beside it and would have copied
    the omission.
    """
    sample = (Sample.objects
              .filter(pk=sample_id)
              .select_related("population__experiment__project")
              .first())
    if sample is None:
        return None, HttpResponse("Sample not found.", status=404)

    experiment = getattr(getattr(sample, "population", None), "experiment", None)
    if not can_view_project(request.user, getattr(experiment, "project", None)):
        # 404 rather than 403: a sample you cannot see should not be distinguishable from one
        # that does not exist, which is the posture `job_cancel` takes for the same reason.
        return None, HttpResponse("Sample not found.", status=404)
    return sample, None


def gd_export_view(request, sample_id):
    """Download the reconstructed .gd for a Sample (gdtools APPLY input)."""
    sample, refusal = _viewable_sample(request, sample_id)
    if refusal is not None:
        return refusal

    gd_text = gd_import.export_gd_text(sample)
    filename = "%s.gd" % (sample.source_name or ("sample_%s" % sample_id))
    response = HttpResponse(gd_text, content_type="text/plain")
    response["Content-Disposition"] = 'attachment; filename="%s"' % filename
    return response


def vcf_export_view(request, sample_id):
    """Download this sample as VCF: the file it was imported from, or one generated from
    its mutations -- see `vcf_export` for what a generated one says it left out."""
    sample, refusal = _viewable_sample(request, sample_id)
    if refusal is not None:
        return refusal

    text = vcf_export.export_vcf_text(sample)
    filename = "%s.vcf" % (sample.source_name or ("sample_%s" % sample_id))
    response = HttpResponse(text, content_type="text/plain")
    response["Content-Disposition"] = 'attachment; filename="%s"' % filename
    return response


def archive_export_view(request, experiment_id):
    """Download the whole experiment as a MutInt archive -- see `mutint_import.archive`.

    Same 404 posture as the sample exports: an experiment you cannot see is not
    distinguishable from one that does not exist. An experiment with no reference has no
    archive, since every mutation in it is defined against one.
    """
    experiment = (Experiment.objects.filter(pk=experiment_id)
                  .select_related("project").first())
    if experiment is None or not can_view_project(request.user, experiment.project):
        return HttpResponse("Experiment not found.", status=404)
    try:
        payload = archive.archive_bytes(experiment)
    except archive.ArchiveError as error:
        return HttpResponse(str(error), status=404)
    response = HttpResponse(payload, content_type="application/zip")
    response["Content-Disposition"] = content_disposition_header(
        True, archive.archive_filename(experiment))
    return response

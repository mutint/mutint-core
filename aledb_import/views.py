import logging

from django.http import HttpResponse, JsonResponse
from django.template import loader
from django.views.decorators.csrf import ensure_csrf_cookie

from aledb_common.util import get_user_context
from aledb_import import gd_import
from aledb_import.forms import GenomeDiffUploadForm
from aledb_seq.models import ResequencingExperiment

logger = logging.getLogger(__name__)


@ensure_csrf_cookie
def gd_import_view(request):
    """Drag-and-drop import of breseq GenomeDiff (.gd) files.

    GET renders the dropzone page. POST accepts a multipart batch of ``.gd`` files
    under ``gd_files`` plus target project/experiment/person fields and returns a
    JSON summary of what was imported.
    """
    if request.method == "POST":
        form = GenomeDiffUploadForm(request.POST)
        uploaded_files = request.FILES.getlist("gd_files")
        if not form.is_valid():
            return JsonResponse({"error": "Invalid form.", "details": form.errors}, status=400)
        gd_files = [f for f in uploaded_files if f.name.lower().endswith(".gd")]
        if not gd_files:
            return JsonResponse({"error": "No .gd files were uploaded."}, status=400)

        person = form.cleaned_data["person"] or request.user.get_username()
        try:
            summary = gd_import.import_gd_files(
                gd_files,
                project_name=form.cleaned_data["project"],
                experiment_name=form.cleaned_data["experiment"],
                person=person)
        except gd_import.ReferenceRequired as exc:
            # A missing precondition is the caller's error, not a server fault.
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("GenomeDiff batch import failed")
            return JsonResponse({"error": str(exc)}, status=500)
        return JsonResponse(summary)

    context = get_user_context(request.user)
    template = loader.get_template("import/gd_import.html")
    return HttpResponse(template.render(context, request), content_type="text/html")


def gd_export_view(request, reseq_id):
    """Download the reconstructed .gd for a ResequencingExperiment (gdtools APPLY input)."""
    try:
        seq_experiment = ResequencingExperiment.objects.get(pk=reseq_id)
    except ResequencingExperiment.DoesNotExist:
        return HttpResponse("Resequencing experiment not found.", status=404)

    gd_text = gd_import.export_gd_text(seq_experiment)
    filename = "%s.gd" % (seq_experiment.sample_name or ("reseq_%s" % reseq_id))
    response = HttpResponse(gd_text, content_type="text/plain")
    response["Content-Disposition"] = 'attachment; filename="%s"' % filename
    return response

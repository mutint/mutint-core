"""What is left of the standalone import views: the .gd export download.

`gd_import_view` (`/import/`) and `reference_views.reference_upload_view`
(`/import/reference/`) lived here until they were retired. Both were unlinked, both went
through the name-based `_prepare_experiment`, whose (name, instrument, person, project)
get_or_create forks a second experiment whenever the person differs, and neither had any
authorization. The Add page (`add_views.py`) plus the chunked session endpoints replaced
them; reference *annotation* replacement, the one capability only the reference page had,
is now the `replace_annotation` import type in `handlers.py`.
"""

from django.http import HttpResponse

from aledb_import import gd_import
from aledb_seq.models import Sample


def gd_export_view(request, sample_id):
    """Download the reconstructed .gd for a Sample (gdtools APPLY input)."""
    try:
        seq_experiment = Sample.objects.get(pk=sample_id)
    except Sample.DoesNotExist:
        return HttpResponse("Resequencing experiment not found.", status=404)

    gd_text = gd_import.export_gd_text(seq_experiment)
    filename = "%s.gd" % (seq_experiment.source_name or ("reseq_%s" % sample_id))
    response = HttpResponse(gd_text, content_type="text/plain")
    response["Content-Disposition"] = 'attachment; filename="%s"' % filename
    return response

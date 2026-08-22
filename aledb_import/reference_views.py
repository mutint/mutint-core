"""Step one of the two-step import: give an experiment its reference genome.

A bare ``.gd`` carries no reference, so it cannot establish one the way a breseq folder can.
This route lets a user upload a GenBank, GFF3, or FASTA up front; everything after that --
including bare ``.gd`` import -- works against an experiment whose reference is known.

Whatever is uploaded is normalized to one canonical pair (a genes-only GFF3 and a FASTA)
before being stored, so a reference supplied here and the same genome arriving inside a
breseq folder hash identically.
"""

import logging
import os
import tempfile

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from aledb_common.util import get_user_context
from aledb_import import reference as reference_io
from aledb_import import reference_store
from aledb_import.forms import ReferenceUploadForm
from aledb_import.gd_import import _prepare_experiment

logger = logging.getLogger("aledb_import.reference_views")

MAX_REFERENCE_BYTES = 512 * 1024 * 1024


@ensure_csrf_cookie
def reference_upload_view(request):
    """GET renders the form; POST creates the experiment and stores its reference."""
    if request.method != "POST":
        context = get_user_context(request.user)
        context["max_reference_mb"] = MAX_REFERENCE_BYTES // (1024 * 1024)
        return render(request, "import/reference_upload.html", context)

    form = ReferenceUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse(
            {"error": "Invalid form.", "details": form.errors}, status=400)

    uploaded = request.FILES.get("reference")
    if uploaded is None:
        return JsonResponse({"error": "No reference file was uploaded."}, status=400)
    if uploaded.size > MAX_REFERENCE_BYTES:
        return JsonResponse(
            {"error": "Reference exceeds %d MB." % (MAX_REFERENCE_BYTES // 1024 // 1024)},
            status=400)

    person = form.cleaned_data["person"] or request.user.get_username()

    suffix = os.path.splitext(uploaded.name)[1]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as staged:
        for block in uploaded.chunks():
            staged.write(block)
        staged_path = staged.name

    try:
        gff3_text, sequences = reference_io.normalize_reference(staged_path, uploaded.name)
    except reference_io.ReferenceFormatError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("reference normalization failed for %s", uploaded.name)
        return JsonResponse(
            {"error": "Could not read %s: %s" % (uploaded.name, exc)}, status=400)
    finally:
        os.unlink(staged_path)

    context = _prepare_experiment(
        form.cleaned_data["project"], form.cleaned_data["experiment"], person,
        form.cleaned_data.get("is_public") or False)
    experiment = context["experiment"]

    try:
        reference, created = reference_store.establish_or_check(
            experiment, gff3_text, sequences,
            replace=form.cleaned_data.get("replace") or False,
            # An explicit upload of the same genome with different annotation is a request
            # to use that annotation.
            update_annotation=True)
    except reference_store.ReferenceMismatch as exc:
        # The experiment already has a different reference. Replacing one under samples
        # that were validated against it is a deliberate act, not a default.
        return JsonResponse(
            {"error": "%s. Tick 'replace' to overwrite it." % exc}, status=409)

    gene_count = sum(1 for line in gff3_text.splitlines()
                     if "\taledb\tgene\t" in line)

    return JsonResponse({
        "experiment_id": experiment.ale_id,
        "experiment": experiment.name,
        "created": created,
        "source_name": uploaded.name,
        "sequences": reference.seq_ids,
        "total_length": reference.total_length,
        "genes": gene_count,
    })

"""Serve BAM/BAI and reference files from the managed store.

Deliberately not built on ``/aledata/`` (``aledb_common.views.protected_file_serve``), which
concatenates ``DOC_ROOT + page_name`` with no normalisation, routes ``.bam``/``.bai`` into a
branch that skips its permission call entirely, and reads whole files into memory with
``f.read()``.

Here every path is derived from a primary key, so no client-supplied path component reaches
the filesystem, and authorization uses the model that actually exists -- ``can_view_project``
-- rather than ``can_view_experiment``, which is a stub returning True.

Range support is hand-rolled: Django 4.2 implements it in neither ``FileResponse`` nor
``django.views.static.serve``, and a genome browser cannot read a BAM without byte ranges.
"""

import os
import re

from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    StreamingHttpResponse,
)

from aledb_common import store
from aledb_experiment.models import AleExperiment
from aledb_experiment.permissions import can_view_project
from aledb_seq.models import ResequencingExperiment

STREAM_CHUNK_BYTES = 512 * 1024

CONTENT_TYPES = {
    store.SAMPLE_BAM: "application/octet-stream",
    store.SAMPLE_BAI: "application/octet-stream",
    store.REFERENCE_FASTA: "text/plain; charset=utf-8",
    store.REFERENCE_FAI: "text/plain; charset=utf-8",
    store.REFERENCE_GFF3: "text/plain; charset=utf-8",
}

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def sample_bam(request, reseq_id):
    return _serve_sample(request, reseq_id, store.SAMPLE_BAM)


def sample_bai(request, reseq_id):
    return _serve_sample(request, reseq_id, store.SAMPLE_BAI)


def reference_fasta(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_FASTA)


def reference_fai(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_FAI)


def reference_gff3(request, experiment_id):
    return _serve_reference(request, experiment_id, store.REFERENCE_GFF3)


def _serve_sample(request, reseq_id, filename):
    try:
        reseq = ResequencingExperiment.objects.get(pk=reseq_id)
    except ResequencingExperiment.DoesNotExist:
        raise Http404("No such resequencing experiment.")

    experiment = reseq.ale_experiment
    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    return _serve_file(request, store.sample_path(reseq.id, filename), filename)


def _serve_reference(request, experiment_id, filename):
    try:
        experiment = AleExperiment.objects.get(pk=experiment_id)
    except AleExperiment.DoesNotExist:
        raise Http404("No such experiment.")

    if not _may_view(request.user, experiment):
        return HttpResponseForbidden("You do not have access to this experiment.")

    return _serve_file(
        request, store.experiment_reference_path(experiment.ale_id, filename), filename)


def _may_view(user, experiment):
    project = getattr(experiment, "project", None)
    if project is None:
        return True
    return can_view_project(user, project)


def _serve_file(request, path, filename):
    if not os.path.isfile(path):
        raise Http404("Not stored for this experiment.")

    size = os.path.getsize(path)
    content_type = CONTENT_TYPES.get(filename, "application/octet-stream")
    range_header = request.META.get("HTTP_RANGE", "")

    if not range_header:
        response = StreamingHttpResponse(
            _iter_file(path, 0, size), content_type=content_type)
        response["Content-Length"] = str(size)
        response["Accept-Ranges"] = "bytes"
        response["Content-Disposition"] = 'inline; filename="%s"' % filename
        return response

    parsed = _parse_range(range_header, size)
    if parsed is None:
        response = HttpResponse(status=416)
        response["Content-Range"] = "bytes */%d" % size
        response["Accept-Ranges"] = "bytes"
        return response

    start, end = parsed
    length = end - start + 1
    response = StreamingHttpResponse(
        _iter_file(path, start, length), status=206, content_type=content_type)
    response["Content-Length"] = str(length)
    response["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = 'inline; filename="%s"' % filename
    return response


def _parse_range(range_header, size):
    """Return an inclusive ``(start, end)`` or None if unsatisfiable.

    Handles the three forms a genome browser sends: ``bytes=A-B``, ``bytes=A-`` (open
    ended), and ``bytes=-N`` (final N bytes). Multi-range is not supported; per RFC 9110 a
    server may serve just the first range, and igv.js never asks for more than one.
    """
    match = _RANGE_RE.match(range_header.strip())
    if not match:
        return None

    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None

    if not first:
        # Suffix form: the final `last` bytes.
        suffix = int(last)
        if suffix == 0:
            return None
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
        end = min(end, size - 1)

    if size == 0 or start >= size or start > end:
        return None
    return start, end


def _iter_file(path, start, length):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            block = handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block

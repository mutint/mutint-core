"""One contract for serving a file off disk: content type, streaming, and byte ranges.

Extracted from ``aledb_sample.views.alignments`` when the legacy ``/aledata/`` route shared it.
That route is gone; this stays as the one file-serving contract for the alignment routes.

Range support is hand-rolled because Django 4.2 implements it in neither ``FileResponse`` nor
``django.views.static.serve``, and a genome browser cannot read a BAM without byte ranges.

Nothing here decides *whether* a file may be served. Callers resolve the path and check
permissions first; these functions assume that has already happened.
"""

import os
import re

from django.http import Http404, HttpResponse, StreamingHttpResponse

STREAM_CHUNK_BYTES = 512 * 1024

EXTENSION_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    # Genomic text formats a browser is expected to read inline.
    ".fasta": "text/plain; charset=utf-8",
    ".fa": "text/plain; charset=utf-8",
    ".fna": "text/plain; charset=utf-8",
    ".fai": "text/plain; charset=utf-8",
    ".gff": "text/plain; charset=utf-8",
    ".gff3": "text/plain; charset=utf-8",
    ".gd": "text/plain; charset=utf-8",
    ".gbk": "text/plain; charset=utf-8",
}

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def content_type_for(filename):
    """Content type from the extension, defaulting to a download rather than a guess.

    Suffix-matched on the real extension: `.bam`, `.bai` and anything unlisted fall through
    to ``application/octet-stream``, which is what a genome browser wants for an alignment.
    """
    _root, extension = os.path.splitext(filename)
    return EXTENSION_CONTENT_TYPES.get(extension.lower(), "application/octet-stream")


def serve_file(request, path, filename, content_type=None):
    """Stream `path` as `filename`, honouring a single Range header.

    404 when the file is not there, 206 + ``Content-Range`` for a satisfiable range, 416 for
    one that is not, 200 otherwise -- always with ``Accept-Ranges: bytes``.
    """
    if not os.path.isfile(path):
        raise Http404("No such file.")

    size = os.path.getsize(path)
    content_type = content_type or content_type_for(filename)
    range_header = request.META.get("HTTP_RANGE", "")

    if not range_header:
        response = StreamingHttpResponse(
            iter_file(path, 0, size), content_type=content_type)
        response["Content-Length"] = str(size)
        response["Accept-Ranges"] = "bytes"
        response["Content-Disposition"] = 'inline; filename="%s"' % filename
        return response

    parsed = parse_range(range_header, size)
    if parsed is None:
        response = HttpResponse(status=416)
        response["Content-Range"] = "bytes */%d" % size
        response["Accept-Ranges"] = "bytes"
        return response

    start, end = parsed
    length = end - start + 1
    response = StreamingHttpResponse(
        iter_file(path, start, length), status=206, content_type=content_type)
    response["Content-Length"] = str(length)
    response["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = 'inline; filename="%s"' % filename
    return response


def parse_range(range_header, size):
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


def iter_file(path, start, length):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            block = handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block

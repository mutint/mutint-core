"""A CSV meant for a spreadsheet starts with the UTF-8 byte-order mark.

Excel opens a `.csv` in the system's legacy code page unless the file starts with the mark
(`\ufeff`), so a file that is correct UTF-8 still showed every arrow, check mark and accent
in a gene column as nonsense. The mark is the one thing Excel takes as "this is UTF-8"
without an import dialog, and every other spreadsheet reads it fine.

**Written at the HTTP boundary only.** `metadata.write` and the archive's `metadata.csv`
stay plain: they are MutInt's own interchange text, read back by `metadata.parse` (which
tolerates a mark but need not meet one), and their first line is pinned by tests. The
mutation matrix's client-side CSV (`mutation_matrix_export.js`) writes `\ufeff` first for the
same effect.
"""

from django.http import HttpResponse
from django.utils.http import content_disposition_header

BOM = "\ufeff"


def for_spreadsheet(text):
    """`text` with the mark in front, for a body built as a string."""
    return BOM + text


def csv_response(filename):
    """An attachment `text/csv; charset=utf-8` response with the mark already written, so
    a caller does `csv.writer(response)` and nothing else."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    response.write(BOM)
    return response

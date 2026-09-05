"""Sample statistics from a breseq run's ``data/summary.json``.

Read by every import path, so it lives on its own rather than inside one of them.
"""

import json
import logging
import os

logger = logging.getLogger("mutint_import.breseq_summary")

SUMMARY_JSON_RELATIVE_PATH = os.path.join("data", "summary.json")


def read_breseq_summary(sample_root):
    """The four sample statistics from breseq's ``data/summary.json``, or None if absent.

    Replaces scraping them out of ``summary.html`` with BeautifulSoup. The JSON carries the
    same numbers as real values, so there is no table-index guessing and no dependence on the
    HTML report existing at all.

    Note the location: ``<sample>/data/summary.json``, a sibling of ``output/`` -- not inside
    it, which is where the HTML lived.
    """
    path = os.path.join(sample_root, SUMMARY_JSON_RELATIVE_PATH)
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            summary = json.load(handle)
    except (ValueError, OSError):
        # Logged rather than swallowed: the old scrape had a bare `except: None` that left
        # the statistics silently at zero.
        logger.exception("could not read breseq summary %s", path)
        return None

    reads = summary.get("reads") or {}
    total_reads = reads.get("total_reads") or 0
    total_bases = reads.get("total_bases") or 0

    return {
        "reads": total_reads,
        "average_read_length": (total_bases / total_reads) if total_reads else 0,
        "percentage_mapped": (reads.get("total_fraction_aligned_reads") or 0) * 100,
        "mean_coverage": mean_coverage(summary),
    }


def mean_coverage(summary):
    """Length-weighted mean coverage across the reference sequences.

    One reference -- the usual case -- reduces to exactly that reference's
    ``coverage_average``. Junction-only entries are not real sequence and are skipped.
    """
    references = ((summary.get("references") or {}).get("reference") or {})

    weighted = 0.0
    total_length = 0
    for reference in references.values():
        if reference.get("junction_only"):
            continue
        length = reference.get("length") or 0
        coverage = reference.get("coverage_average") or 0
        weighted += coverage * length
        total_length += length

    return (weighted / total_length) if total_length else 0

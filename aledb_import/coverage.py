"""Derive a per-sample coverage BigWig from its stored alignment.

igv computes a coverage row itself, but it does so from the reads it has fetched, and that row
is part of the alignment track -- so `checkZoomIn` takes it away with the reads once the view
is wider than the track's visibility window. Looking at a whole genome then leaves the choice
between no coverage and loading reads across the lot, which is what exhausts the tab.

A BigWig has its own zoom levels, so one file answers a whole-genome question and a base-pair
one alike, over the byte-range serving the store already does. It is what lets the browser page
stop loading reads above 4 kb without losing the coverage.

Two external tools, declared in aledb-core's tools.txt and found through aledb_common.tools:

    bedtools genomecov -ibam aligned.bam -bg   ->  bedGraph of depth
    sort -k1,1 -k2,2n                          ->  bedGraphToBigWig will not take it unsorted
    bedGraphToBigWig - chrom.sizes coverage.bw

Not megadepth, which would do the lot in one call: bioconda has no osx-arm64 build of it.
"""

import logging
import os
import subprocess
import tempfile

from aledb_common import store
from aledb_common.tools import ToolMissing, require

logger = logging.getLogger(__name__)

BEDTOOLS = "bedtools"
BEDGRAPH_TO_BIGWIG = "bedGraphToBigWig"

# A sample's whole genome is walked, so this is not instant; it is bounded so a wedged tool
# cannot hold an import request open indefinitely.
TIMEOUT_SECONDS = 900


class CoverageError(RuntimeError):
    """The coverage file could not be derived for this sample."""


def chrom_sizes_from_fai(fai_path, out_path):
    """Write a chrom.sizes from a samtools .fai -- its first two columns, nothing else.

    The experiment's reference already has one, written by `aledb_import.reference.write_fai`
    at store time, so this needs no new input and cannot disagree with what igv is served.
    """
    with open(fai_path) as source, open(out_path, "w") as target:
        for line in source:
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            target.write("%s\t%s\n" % (fields[0], fields[1].strip()))
    if os.path.getsize(out_path) == 0:
        raise CoverageError("reference index %s named no sequences" % fai_path)
    return out_path


def build_for(reseq):
    """Derive and store the BigWig for one ResequencingExperiment; set `coverage_stored`.

    Raises CoverageError (or ToolMissing) rather than returning a flag, so a caller that wants
    it to be best-effort has to say so.
    """
    if not reseq.bam_stored:
        raise CoverageError("sample %s has no stored alignment" % reseq.id)

    bam_path = store.sample_path(reseq.id, store.SAMPLE_BAM)
    if not os.path.isfile(bam_path):
        raise CoverageError("sample %s is flagged bam_stored but %s is missing"
                            % (reseq.id, bam_path))

    experiment = reseq.ale_experiment
    fai_path = store.experiment_reference_path(experiment.ale_id, store.REFERENCE_FAI)
    if not os.path.isfile(fai_path):
        raise CoverageError(
            "experiment %s has no stored reference index; coverage needs one for the "
            "sequence lengths" % experiment.ale_id)

    bedtools = require(BEDTOOLS)
    to_bigwig = require(BEDGRAPH_TO_BIGWIG)

    out_path = store.sample_path(reseq.id, store.SAMPLE_BIGWIG)
    store.ensure_dir(store.sample_dir(reseq.id))

    with tempfile.TemporaryDirectory() as scratch:
        sizes = chrom_sizes_from_fai(fai_path, os.path.join(scratch, "chrom.sizes"))
        bedgraph = os.path.join(scratch, "coverage.bedgraph")

        # Piped through sort rather than trusting the BAM's order: bedGraphToBigWig rejects
        # unsorted input outright, and a coordinate-sorted BAM still emits its references in
        # header order, which is not the byte order sort wants. LC_ALL=C so the collation is
        # the byte order it asks for whatever the machine's locale is.
        # `bedtools` is a dispatcher: the subcommand comes before its own flags.
        _run([bedtools, "genomecov", "-ibam", bam_path, "-bg"], stdout_path=bedgraph)
        _sort_in_place(bedgraph, scratch)

        # Written to a temporary name and moved into place, so an interrupted run cannot
        # leave a half-written BigWig that looks like a good one.
        staged = os.path.join(scratch, "coverage.bw")
        _run([to_bigwig, bedgraph, sizes, staged])
        os.replace(staged, out_path)

    reseq.coverage_stored = True
    reseq.save(update_fields=["coverage_stored"])
    return out_path


def build_quietly(reseq):
    """`build_for`, reporting failure rather than raising. Returns True when it wrote one.

    This is the import path's contract: coverage is worth having and not worth rejecting a
    sample over. A sample without it keeps its reads and gets a BigWig from
    ``./aledb coverage`` later.
    """
    try:
        build_for(reseq)
        return True
    except (CoverageError, ToolMissing, subprocess.SubprocessError, OSError) as error:
        logger.warning("no coverage track for sample %s: %s", reseq.id, error)
        return False


def _sort_in_place(path, scratch):
    sorted_path = os.path.join(scratch, "sorted.bedgraph")
    environment = dict(os.environ, LC_ALL="C")
    with open(path) as source, open(sorted_path, "w") as target:
        subprocess.run(["sort", "-k1,1", "-k2,2n"], stdin=source, stdout=target,
                       check=True, timeout=TIMEOUT_SECONDS, env=environment)
    os.replace(sorted_path, path)


def _run(argv, stdout_path=None):
    """One tool, with its stderr kept for the error message."""
    target = open(stdout_path, "w") if stdout_path else subprocess.DEVNULL
    try:
        finished = subprocess.run(argv, stdout=target, stderr=subprocess.PIPE,
                                  timeout=TIMEOUT_SECONDS)
    finally:
        if stdout_path:
            target.close()

    if finished.returncode != 0:
        raise CoverageError("%s failed: %s" % (
            os.path.basename(argv[0]),
            finished.stderr.decode("utf-8", "replace").strip() or
            "exit status %d" % finished.returncode))

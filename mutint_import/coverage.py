"""Derive a per-sample coverage BigWig from its stored alignment.

igv computes a coverage row itself, but it does so from the reads it has fetched, and that row
is part of the alignment track -- so `checkZoomIn` takes it away with the reads once the view
is wider than the track's visibility window. Looking at a whole genome then leaves the choice
between no coverage and loading reads across the lot, which is what exhausts the tab.

A BigWig has its own zoom levels, so one file answers a whole-genome question and a base-pair
one alike, over the byte-range serving the store already does. It is what lets the browser page
stop loading reads above 4 kb without losing the coverage.

**Each alignment counts 1/X1, not 1.** X1 is breseq's redundancy tag: the number of places a
read mapped equally well. A read matching all ten copies of an IS element is written to the BAM
ten times, so counting each as 1 gives every copy ten times the true depth and repeats dominate
the trace. Weighting by 1/X1 is breseq's own rule -- `coverage_output.cpp` accumulates
`unique_cov++` for redundancy 1 and `redundant_cov += 1.0/redundancy` otherwise, and its total
is the sum of the two, which is what this file computes.

**A read with no X1 counts 1**, which is also breseq's rule (`alignment.cpp`: "Defaults to 1
when custom breseq tag is missing"). So a BAM from anything but breseq gives exactly what it
gave before this existed -- there is a test asserting that against the old pipeline's output.

`bedtools genomecov` cannot do this: its `-scale` is one factor for the whole file, not per
read. So the counting happens here, over pysam, and only the BigWig conversion stays external:

    pysam + a difference array                 ->  bedGraph of weighted depth
    sort -k1,1 -k2,2n                          ->  bedGraphToBigWig will not take it unsorted
    bedGraphToBigWig - chrom.sizes coverage.bw

`bedtools bamtobed -tag X1` looks like it would do the job with the tool that was already
installed. It must not be used: on a BAM whose reads lack the tag it writes a partial line,
prints its error into the middle of its own stdout, and exits 0.

Not megadepth, which would do the lot in one call: bioconda has no osx-arm64 build of it.
"""

import logging
import os
import subprocess
import tempfile

from mutint_common import store
from mutint_common.tools import ToolMissing, require

logger = logging.getLogger(__name__)

BEDGRAPH_TO_BIGWIG = "bedGraphToBigWig"

#: breseq's redundancy tag -- how many places this read mapped equally well.
REDUNDANCY_TAG = "X1"

# A sample's whole genome is walked, so this is not instant; it is bounded so a wedged tool
# cannot hold an import request open indefinitely.
TIMEOUT_SECONDS = 900


class CoverageError(RuntimeError):
    """The coverage file could not be derived for this sample."""


def chrom_sizes_from_fai(fai_path, out_path):
    """Write a chrom.sizes from a samtools .fai -- its first two columns, nothing else.

    The experiment's reference already has one, written by `mutint_import.reference.write_fai`
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


class CoverageTally:
    """What the sweep saw, so a caller can say whether normalization did anything.

    There is no column recording which rule a stored BigWig was built under, so this is the
    only thing that can answer "is my coverage actually normalized". Without it, an IS element
    still towering over the trace could mean the BigWig predates this change, or the BAM has no
    X1, or the weighting is broken -- three very different problems that look identical.

    Counting is free: the sweep visits every record anyway.
    """

    def __init__(self):
        self.alignments = 0        # mapped alignments counted into the depth
        self.tagged = 0            # ...of which carried X1 at all
        self.redundant = 0         # ...of which carried X1 > 1
        self.redundancy_total = 0  # sum of X1 over tagged reads, for the mean

    @property
    def normalized(self):
        """Did any read actually weigh less than 1? If not, this is the old answer."""
        return self.redundant > 0

    def describe(self):
        if not self.alignments:
            return "no mapped alignments"
        if not self.tagged:
            return "no X1 tags -- coverage is NOT normalized"
        share = 100.0 * self.redundant / self.alignments
        mean = self.redundancy_total / self.tagged
        return ("%s alignments, %.1f%% redundant, mean redundancy %.1f"
                % (f"{self.alignments:,}", share, mean))


def write_weighted_bedgraph(bam_path, sizes, out_path):
    """Walk the BAM once, weighting each alignment by 1/X1, and write a bedGraph.

    A **difference array** per contig rather than adding across each read's span: `+w` at the
    start and `-w` at the end is O(1) per read where the span walk is O(read length), and one
    cumulative sum at the end turns it back into depth. Float, because the weights are.

    Only unmapped reads are skipped. Secondary and supplementary alignments are counted,
    because `bedtools genomecov -ibam` counted them and this must differ from the old pipeline
    in the weighting and nothing else -- filtering them here would quietly change every
    existing trace at the same time as the change actually being made.

    `until_eof=True` reads the file through rather than seeking by region, so no BAM index is
    needed and every record is seen exactly once.
    """
    # numpy and pysam are imported here rather than at module scope, and it is not style.
    # Test discovery imports every test module, so a module-level import here is loaded into
    # the process before any test runs -- and numpy's BLAS brings up a thread pool as it
    # loads. `test_concurrent_imports` measures a race between two SQLite writers, and that
    # extra process state was enough to stop the race reproducing, failing a test that runs
    # long before this code does. Deferring the import keeps a module nothing has called out
    # of the process, which is also why a deployment that never derives coverage does not pay
    # htslib's load.
    import numpy

    lengths = _sizes_map(sizes)
    tally = CoverageTally()
    # One extra cell so a read ending at the last base has somewhere to write its -w.
    deltas = {name: numpy.zeros(length + 1, dtype=numpy.float64)
              for name, length in lengths.items()}

    try:
        _accumulate(bam_path, deltas, tally)
    except (ValueError, OSError) as error:
        # pysam raises ValueError for a file whose header will not parse -- "file does not
        # have a valid header ... is it BAM/CRAM format?" -- and that has to arrive as this
        # module's own error, or it escapes `build_quietly` and fails the whole sample import
        # over a coverage track. A sample keeps its reads whether or not its coverage builds.
        raise CoverageError("could not read %s: %s" % (os.path.basename(bam_path), error))

    with open(out_path, "w") as target:
        for name in sorted(deltas):
            _emit_contig(target, name, numpy.cumsum(deltas[name])[:-1])
    return tally


def _accumulate(bam_path, deltas, tally):
    """The walk itself, split out so its failures have one place to be translated."""
    import pysam

    with pysam.AlignmentFile(bam_path, "rb") as bam:
        for read in bam.fetch(until_eof=True):
            if read.is_unmapped or read.reference_name is None:
                continue
            delta = deltas.get(read.reference_name)
            if delta is None:
                # A contig the reference index does not name. bedGraphToBigWig would reject
                # the row anyway; dropping it here keeps the failure to this one read.
                continue
            start, end = read.reference_start, read.reference_end
            if end is None or end <= start:
                continue

            weight = 1.0
            if read.has_tag(REDUNDANCY_TAG):
                # get_tag goes through htslib, which decodes whichever integer width the tag
                # was stored in. breseq writes `X1:i:` as SAM text, but htslib stores an
                # integer aux tag in the smallest type that fits, so in the file it is
                # usually `C`. Matching on one type by hand is how this silently reads no
                # tags at all and produces the unnormalized answer while reporting success.
                redundancy = int(read.get_tag(REDUNDANCY_TAG))
                tally.tagged += 1
                tally.redundancy_total += redundancy
                if redundancy > 1:
                    tally.redundant += 1
                    weight = 1.0 / redundancy

            tally.alignments += 1
            delta[start] += weight
            delta[end] -= weight


def _emit_contig(target, name, depth):
    """bedGraph rows for one contig: runs of equal non-zero depth.

    The run boundaries come from `numpy.diff` rather than a Python loop over every base --
    a 4.6 Mb contig is 4.6 million iterations otherwise, per sample.

    Imported here as well as in the caller; see the note there for why it is not at module
    scope. After the first import this is a dict lookup.
    """
    import numpy

    if not depth.size:
        return
    # A boundary wherever the value changes, plus the two ends.
    edges = numpy.flatnonzero(numpy.diff(depth)) + 1
    starts = numpy.concatenate(([0], edges))
    ends = numpy.concatenate((edges, [depth.size]))
    for start, end in zip(starts.tolist(), ends.tolist()):
        value = depth[start]
        if value <= 0:
            continue
        # %g so a whole number prints as `3` and not `3.0`: that is what bedtools emitted,
        # and an untagged BAM has to produce the identical file.
        target.write("%s\t%d\t%d\t%g\n" % (name, start, end, value))


def _sizes_map(sizes_path):
    lengths = {}
    with open(sizes_path) as handle:
        for line in handle:
            fields = line.split("\t")
            if len(fields) >= 2:
                lengths[fields[0]] = int(fields[1])
    return lengths


def build_for(reseq):
    """Derive and store the BigWig for one Sample; set `coverage_stored`.

    Returns a `CoverageTally` describing what the BAM turned out to hold -- how much of it was
    redundantly mapped, and whether it carried X1 at all. It returned the output path before,
    which nothing read.

    Raises CoverageError (or ToolMissing) rather than returning a flag, so a caller that wants
    it to be best-effort has to say so.
    """
    if not reseq.bam_stored:
        raise CoverageError("sample %s has no stored alignment" % reseq.id)

    bam_path = store.sample_path(reseq.id, store.SAMPLE_BAM)
    if not os.path.isfile(bam_path):
        raise CoverageError("sample %s is flagged bam_stored but %s is missing"
                            % (reseq.id, bam_path))

    experiment = reseq.experiment
    fai_path = store.experiment_reference_path(experiment.id, store.REFERENCE_FAI)
    if not os.path.isfile(fai_path):
        raise CoverageError(
            "experiment %s has no stored reference index; coverage needs one for the "
            "sequence lengths" % experiment.id)

    to_bigwig = require(BEDGRAPH_TO_BIGWIG)

    out_path = store.sample_path(reseq.id, store.SAMPLE_BIGWIG)
    store.ensure_dir(store.sample_dir(reseq.id))

    with tempfile.TemporaryDirectory() as scratch:
        sizes = chrom_sizes_from_fai(fai_path, os.path.join(scratch, "chrom.sizes"))
        bedgraph = os.path.join(scratch, "coverage.bedgraph")

        # Sorted rather than trusted: bedGraphToBigWig rejects unsorted input outright, and
        # contig order here is the reference index's, which is not the byte order sort wants.
        # LC_ALL=C so the collation is that byte order whatever the machine's locale is.
        tally = write_weighted_bedgraph(bam_path, sizes, bedgraph)
        _sort_in_place(bedgraph, scratch)

        # Written to a temporary name and moved into place, so an interrupted run cannot
        # leave a half-written BigWig that looks like a good one.
        staged = os.path.join(scratch, "coverage.bw")
        _run([to_bigwig, bedgraph, sizes, staged])
        os.replace(staged, out_path)

    reseq.coverage_stored = True
    reseq.save(update_fields=["coverage_stored"])
    logger.info("coverage for sample %s: %s", reseq.id, tally.describe())
    return tally


def build_quietly(reseq):
    """`build_for`, reporting failure rather than raising. Returns True when it wrote one.

    This is the import path's contract: coverage is worth having and not worth rejecting a
    sample over. A sample without it keeps its reads and gets a BigWig from
    ``./mutint coverage`` later.
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

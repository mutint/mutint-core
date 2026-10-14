"""Where a mutation sits on the reference, shared by every page that draws it.

A pure module, in the shape `functional_change.py` was extracted into and for the same
reason: `views/common.py` imports django.http, django.template and the permissions layer at
import time, and "which bases does this mutation cover" should not require any of that.

There is one derivation of the extent and every drawing page uses it. Two would be worse than
it sounds -- the genome browser and the NCBI viewer would then be able to disagree about what
a deletion covers while both looking correct, and the annotator's own interval rule is the
thing they are both supposed to be echoing.
"""

from mutint_import.annotate.annotator import mutation_interval

#: How much context to show either side of the mutation's own extent. A view bounded by the
#: mutation alone would put its ends at the very edge, and for a deletion the two junctions
#: are the part worth seeing.
LOCUS_BUFFER_BASES = 200


def mutation_extent(mutation):
    """The reference interval the mutation occupies, 1-based inclusive.

    `start_position`/`end_position` are what the annotator already wrote from breseq's own
    rule (`mutation_interval`, the port of `cDiffEntry::get_reference_coordinate_start`/
    `_end`), so they are used as-is. A mutation imported before a reference was available has
    neither, and is measured from its raw GenomeDiff record by that same function rather than by a
    second derivation that could disagree with it.
    """
    if mutation.start_position and mutation.end_position:
        return mutation.start_position, mutation.end_position
    if mutation.genome_diff:
        return mutation_interval(mutation.genome_diff)
    return mutation.start_position, mutation.start_position


def buffered_extent(mutation, contig_length=None, buffer_bases=LOCUS_BUFFER_BASES):
    """`mutation_extent` widened by `buffer_bases`, clamped to the contig.

    `contig_length` comes from `ReferenceSequences.seq_ids`, which carries a length per
    contig -- so clamping the right-hand end costs no file read. Passing None leaves that end
    unclamped, which is what the genome browser does: igv resolves an over-long end against
    the FASTA index it is already loading, and NCBI's viewer does not.
    """
    start, end = mutation_extent(mutation)
    low = max(1, start - buffer_bases)
    high = end + buffer_bases
    if contig_length:
        high = min(high, contig_length)
        low = min(low, contig_length)
    return low, high


def parse_locus(text):
    """`"contig:start-end"` -> `(contig, start, end)`, 1-based inclusive, or `ValueError`.

    The spelling igv itself takes, with the thousands separators it tolerates stripped. The
    end may be omitted (`contig:start`) and is then the start. Nothing here knows the
    reference; a caller checks the contig against it.
    """
    contig, sep, span = (text or "").strip().rpartition(":")
    if not sep or not contig:
        raise ValueError("a locus is spelled contig:start-end")
    first, _, last = span.replace(",", "").partition("-")
    try:
        start = int(first)
        end = int(last) if last else start
    except ValueError:
        raise ValueError("a locus is spelled contig:start-end") from None
    if start < 1 or end < start:
        raise ValueError("a locus runs from 1 and ends at or after it starts")
    return contig, start, end


def format_locus(contig, start, end, buffer_bases=LOCUS_BUFFER_BASES):
    """`contig:start-end` widened by `buffer_bases`, as the browser is handed it."""
    return "%s:%d-%d" % (contig, max(1, start - buffer_bases), end + buffer_bases)

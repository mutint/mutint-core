"""
A reference, or some of its contigs, rendered for download.

The store keeps one canonical pair per experiment -- breseq-dialect GFF3 with the sequence
inline, and the FASTA -- and nothing else: the file somebody uploaded is not kept. So every
download is *rendered* from the loaded annotation model rather than served from disk, which
is what makes a subset of contigs and a third format possible at all. For the whole reference
in FASTA or GFF3 the bytes come out equal to the stored files, because `render_fasta` and
`render_breseq_gff3` are what wrote them.

GenBank is the format the store never held. It is written with Biopython from the same model
the GenBank *reader* (`annotate/genbank.py`) fills, and the qualifier mapping here is that
reader's inverted: what this writes, that reads back to the same features. It is a reduced
GenBank by construction -- the model is genes-only, so there is no `source` feature, no
organism, no `/translation`, and only the qualifiers breseq annotates from.

Pure: no Django, no store, no request. `mutint_sample.views.alignments.reference_download`
is the caller.
"""

import collections
import io
import re
import warnings

from Bio import BiopythonWarning, SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import (
    AfterPosition,
    BeforePosition,
    CompoundLocation,
    SeqFeature,
    SimpleLocation,
)
from Bio.SeqRecord import SeqRecord

from mutint_import import reference
from mutint_import.annotate import gff3
from mutint_import.annotate.model import INSERTION_SEQUENCE_PREFIX, LoadedReferenceSequences

Format = collections.namedtuple("Format", "key label extension content_type")

FORMATS = collections.OrderedDict((fmt.key, fmt) for fmt in (
    Format("fasta", "FASTA", ".fasta", "text/plain; charset=utf-8"),
    Format("gff3", "GFF3 (breseq, with sequence)", ".gff3", "text/plain; charset=utf-8"),
    Format("genbank", "GenBank", ".gbk", "text/plain; charset=utf-8"),
))
DEFAULT_FORMAT = "fasta"


class UnknownSequence(ValueError):
    """A requested seq_id that the reference does not carry. The message names them."""


def subset(references, seq_ids):
    """The contigs named by `seq_ids`, as a new `LoadedReferenceSequences`.

    An empty `seq_ids` means every distinct contig. Repeats collapse; an id the reference
    does not carry raises `UnknownSequence` naming every unknown one, since a download of
    "the two plasmids" that quietly held one of them is worse than a refusal.

    Always a **new** container holding the *same* `AnnotatedSequence` objects: the input is
    normally the object `annotation.reference_sequences_for` memoises per process, and
    dropping contigs out of it would change what the next import annotates against.
    """
    if not seq_ids:
        seq_ids = [seq_id for seq_id, _ in reference.sequences_of(references)]

    unknown = [seq_id for seq_id in seq_ids if seq_id not in references]
    if unknown:
        raise UnknownSequence(
            "This reference has no sequence called %s." % ", ".join(sorted(set(unknown))))

    chosen = LoadedReferenceSequences()
    seen = set()
    for seq_id in seq_ids:
        contig = references[seq_id]
        # `references` may register one contig under an alias as well as its name; either
        # spelling selects it, and it is added once, under its canonical name.
        if id(contig) in seen:
            continue
        seen.add(id(contig))
        chosen.add(contig)
    return chosen


def is_complete(references, chosen):
    """Whether `chosen` holds every distinct contig of `references`."""
    return len(reference.sequences_of(chosen)) == len(reference.sequences_of(references))


def render(references, fmt, definition=""):
    """`references` as the text of format `fmt` (a `FORMATS` key)."""
    if fmt == "fasta":
        return render_fasta(references)
    if fmt == "gff3":
        return render_gff3(references)
    if fmt == "genbank":
        return render_genbank(references, definition=definition)
    raise KeyError(fmt)


def render_fasta(references):
    return reference.render_fasta(reference.sequences_of(references))


def render_gff3(references):
    return gff3.render_breseq_gff3(references)


def render_genbank(references, definition=""):
    """GenBank text, one record per contig.

    Biopython warns when a LOCUS name runs past the 16 characters the fixed-width header
    allots and then widens the line correctly anyway, as `genbank.parse_records` notes for
    the reader. Silenced for the same reason: it is not a problem anybody can act on.
    """
    out = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", BiopythonWarning)
        SeqIO.write(to_genbank_records(references, definition=definition), out, "genbank")
    return out.getvalue()


def to_genbank_records(references, definition=""):
    """Biopython `SeqRecord`s for the distinct contigs, in `sequences_of`'s order."""
    records = []
    for seq_id, _ in reference.sequences_of(references):
        contig = references[seq_id]
        record = SeqRecord(
            Seq(contig.sequence), id=seq_id, name=seq_id,
            description=definition or seq_id,
            annotations={"molecule_type": "DNA"})
        record.features = [_genbank_feature(feature)
                           for feature in contig.features if feature.locations]
        records.append(record)
    return records


def _genbank_feature(feature):
    return SeqFeature(_genbank_location(feature), type=feature.type,
                      qualifiers=_qualifiers(feature))


def _genbank_location(feature):
    """The model's 1-based inclusive sublocations as Biopython's 0-based half-open ones.

    Parts are handed over in the model's order, which is 5'->3' along the feature -- the
    order `genbank._locations_from_biopython` reads `.parts` back in, and the order Biopython
    itself keeps: it writes a minus-strand join as `complement(join(low..high, ...))` by
    reversing on the way out and reversing again on the way in.
    """
    parts = []
    for location in feature.locations:
        start = location.start_1 - 1
        end = location.end_1
        if location.start_is_indeterminate:
            start = BeforePosition(start)
        if location.end_is_indeterminate:
            end = AfterPosition(end)
        parts.append(SimpleLocation(start, end, strand=location.strand))
    return parts[0] if len(parts) == 1 else CompoundLocation(parts)


def _qualifiers(feature):
    """`annotate/genbank.py:_build_feature` inverted, so a reload finds the same values.

    Two of the reader's fallbacks decide what is *not* written. Its gene name is
    `/gene` > `/locus_tag`, and the GFF3 loader fills a nameless gene's `name` with its
    locus tag -- so writing that name as `/gene` would turn a locus tag into a gene symbol
    on every locus that never had one. And a repeat's name reaches the reader through
    `trim_repeat_name`, which strips an `insertion sequence:` prefix only from the front and
    is a no-op on a name already trimmed, which every stored repeat name is.
    """
    qualifiers = collections.OrderedDict()
    if feature.is_repeat():
        if feature.type == "mobile_element":
            qualifiers["mobile_element_type"] = [INSERTION_SEQUENCE_PREFIX + feature.name]
        elif feature.name:
            qualifiers["rpt_family"] = [feature.name]
        if feature.product:
            qualifiers["note"] = [feature.product]
        return qualifiers

    if feature.name and feature.name != "unknown" and feature.name != feature.locus_tag:
        qualifiers["gene"] = [feature.name]
    if feature.locus_tag:
        qualifiers["locus_tag"] = [feature.locus_tag]
    if feature.product:
        qualifiers["product"] = [feature.product]
    if feature.pseudogene:
        qualifiers["pseudo"] = [None]  # a bare /pseudo, which is how the reader tests it
    if feature.type == "CDS":
        qualifiers["transl_table"] = [str(feature.translation_table)]
    return qualifiers


_UNSAFE_IN_FILENAME = re.compile(r"[\s/\\]+")


def filename(experiment_name, seq_ids, fmt, complete):
    """What the download is called.

    The whole reference is `<experiment>_reference`, one contig is named for the contig, and
    several are counted. Only whitespace and path separators are replaced: a dotted contig
    name like `NC_000913.3` is worth keeping, and quoting the result for the header is
    `django.utils.http.content_disposition_header`'s job, not this one's.
    """
    extension = FORMATS[fmt].extension
    if complete:
        stem = "%s_reference" % experiment_name
    elif len(seq_ids) == 1:
        stem = "%s_%s" % (experiment_name, seq_ids[0])
    else:
        stem = "%s_%d_sequences" % (experiment_name, len(seq_ids))
    return _UNSAFE_IN_FILENAME.sub("_", stem.strip()) + extension

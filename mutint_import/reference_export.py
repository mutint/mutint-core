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
reader's inverted: what this writes, that reads back to the same features. The model keeps
every qualifier and feature type the file had, and the record's header besides, so what comes
out resembles what went in -- with two things rebuilt rather than stored: the `gene` feature
over each locus, and `/translation`.

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
from mutint_import.annotate.codon_tables import STOP, UNKNOWN_AMINO_ACID, translate_codon
from mutint_import.annotate.model import (
    INSERTION_SEQUENCE_PREFIX,
    REPEAT_TYPES,
    LoadedReferenceSequences,
)

Format = collections.namedtuple("Format", "key label extension content_type")

FORMATS = collections.OrderedDict((fmt.key, fmt) for fmt in (
    Format("fasta", "FASTA", ".fasta", "text/plain; charset=utf-8"),
    Format("gff3", "GFF3 (breseq, with sequence)", ".gff3", "text/plain; charset=utf-8"),
    Format("genbank", "GenBank", ".gbk", "text/plain; charset=utf-8"),
))
DEFAULT_FORMAT = "fasta"

#: Record-level `metadata` keys that are header lines rather than `source` qualifiers.
HEADER_KEYS = frozenset(("Note", "taxonomy", "comment", "date", "keywords", "division"))

#: What a synthesized `gene` feature carries of its child besides the symbol, the locus tag
#: and `pseudo`, in NCBI's order.
GENE_FEATURE_QUALIFIERS = ("old_locus_tag", "gene_synonym", "db_xref")

#: Types that are not a locus's child and get no `gene` written over them.
NO_GENE_FEATURE_TYPES = frozenset(
    ("gene", "regulatory", "misc_feature", "source", "region")) | REPEAT_TYPES


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
    """Biopython `SeqRecord`s for the distinct contigs, in `sequences_of`'s order.

    The header and the `source` feature come from the contig's `metadata` where the file it
    came from had them; otherwise the DEFINITION is `definition` (the experiment's name) and
    there is no `source`. A `gene` feature is written over every locus, since the loaders
    fold a GenBank's gene into the feature under it.
    """
    records = []
    for seq_id, _ in reference.sequences_of(references):
        contig = references[seq_id]
        metadata = contig.metadata or {}
        annotations = {"molecule_type": "DNA"}
        if contig.circular is not None:
            # Only when known: Biopython writes a blank LOCUS topology field for an
            # absent key, and the GenBank loader reads that back as None, so an unknown
            # topology survives a round trip rather than hardening into "linear".
            annotations["topology"] = "circular" if contig.circular else "linear"
        if metadata.get("organism"):
            annotations["organism"] = metadata["organism"][0]
            annotations["source"] = metadata["organism"][0]
        for key, biopython_key in (("taxonomy", "taxonomy"), ("keywords", "keywords"),
                                   ("date", "date"), ("division", "data_file_division"),
                                   ("comment", "comment")):
            if metadata.get(key):
                values = list(metadata[key])
                annotations[biopython_key] = (
                    values if biopython_key in ("taxonomy", "keywords") else "\n".join(values))
        record = SeqRecord(
            Seq(contig.sequence), id=seq_id, name=seq_id,
            description=(metadata.get("Note") or [definition or seq_id])[0],
            annotations=annotations)
        record.features = _genbank_features(contig)
        records.append(record)
    return records


def _genbank_features(contig):
    features = []
    source = _source_feature(contig)
    if source is not None:
        features.append(source)
    # A gene the file had over a span no feature shares was kept as a `gene` row; its
    # locus gets no second, synthesized one.
    kept_genes = {f.locus_tag for f in contig.features if f.type == "gene" and f.locus_tag}
    for feature in contig.features:
        if not feature.locations:
            continue
        if feature.locus_tag not in kept_genes:
            gene = _gene_feature(feature)
            if gene is not None:
                features.append(gene)
        features.append(_genbank_feature(feature, contig))
    return features


def _source_feature(contig):
    """A `source` over the whole contig, from the record-level metadata that is not a header
    line; None where the file said nothing about its source."""
    qualifiers = collections.OrderedDict()
    for key, values in (contig.metadata or {}).items():
        if key not in HEADER_KEYS:
            qualifiers[key] = list(values)
    if not qualifiers:
        return None
    if "mol_type" not in qualifiers:
        qualifiers["mol_type"] = ["genomic DNA"]
        qualifiers.move_to_end("mol_type", last=False)
    if "organism" in qualifiers:
        qualifiers.move_to_end("organism", last=False)
    return SeqFeature(SimpleLocation(0, len(contig), strand=1), type="source",
                      qualifiers=qualifiers)


def _gene_feature(feature):
    """The `gene` a GenBank writes over a locus, synthesized from its child.

    The loaders fold a file's gene into the CDS or RNA at its span, so what comes back is
    the NCBI shape -- the symbol, the locus tag, `old_locus_tag`, `gene_synonym`, `db_xref`
    and `pseudo` -- over the child's outer span, for every feature that carries a locus tag.
    An approximation of the file's own gene set, by construction: a gene the file had with
    no feature under it is kept as a `gene` row and written as itself.
    """
    if feature.type in NO_GENE_FEATURE_TYPES or not feature.locus_tag:
        return None
    qualifiers = collections.OrderedDict()
    if _has_symbol(feature):
        qualifiers["gene"] = [feature.name]
    qualifiers["locus_tag"] = [feature.locus_tag]
    for key in GENE_FEATURE_QUALIFIERS:
        if feature.attributes.get(key):
            qualifiers[key] = list(feature.attributes[key])
    if feature.pseudogene:
        qualifiers["pseudo"] = [None]
    first = min(feature.locations, key=lambda loc: loc.start_1)
    last = max(feature.locations, key=lambda loc: loc.end_1)
    start = first.start_1 - 1
    end = last.end_1
    if first.start_is_indeterminate:
        start = BeforePosition(start)
    if last.end_is_indeterminate:
        end = AfterPosition(end)
    return SeqFeature(SimpleLocation(start, end, strand=feature.locations[0].strand),
                      type="gene", qualifiers=qualifiers)


def _has_symbol(feature):
    return bool(feature.name) and feature.name != "unknown" \
        and feature.name != feature.locus_tag


def _genbank_feature(feature, contig=None):
    qualifiers = _qualifiers(feature)
    translation = _translation(feature, contig) if contig is not None else None
    if translation:
        qualifiers["translation"] = [translation]
    return SeqFeature(_genbank_location(feature), type=feature.type, qualifiers=qualifiers)


def _translation(feature, contig):
    """The protein a non-pseudo CDS encodes, regenerated from the stored sequence.

    `/translation` is not stored: it is the largest qualifier in a GenBank and is derived
    from the bases, the table and `codon_start`. Codon 1 takes the initiator table unless
    the start is indeterminate, a trailing stop is dropped, a trailing partial codon is
    ignored, and a CDS whose reading has a stop or an unknown inside it -- ribosomal
    slippage, a `transl_except`, a mis-set frame -- gets none rather than a wrong one.
    """
    if feature.type != "CDS" or feature.pseudogene:
        return None
    try:
        offset = int(feature.attributes.get("codon_start", ["1"])[0]) - 1
    except ValueError:
        offset = 0
    bases = feature.get_nucleotide_sequence(contig)[max(offset, 0):]
    if len(bases) < 3:
        return None
    amino_acids = []
    for index in range(0, len(bases) - len(bases) % 3, 3):
        codon_number_1 = 2 if (index or feature.start_is_indeterminate()) else 1
        amino_acids.append(translate_codon(bases[index:index + 3],
                                           feature.translation_table, codon_number_1))
    if amino_acids and amino_acids[-1] == STOP and not feature.end_is_indeterminate():
        amino_acids.pop()
    protein = "".join(amino_acids)
    if not protein or STOP in protein or UNKNOWN_AMINO_ACID in protein:
        return None
    return protein


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

    The mapped fields first, then every attribute the model did not interpret, verbatim.
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
        _add_attributes(qualifiers, feature)
        return qualifiers

    if _has_symbol(feature):
        qualifiers["gene"] = [feature.name]
    if feature.locus_tag:
        qualifiers["locus_tag"] = [feature.locus_tag]
    if feature.product:
        qualifiers["product"] = [feature.product]
    if feature.pseudogene:
        qualifiers["pseudo"] = [None]  # a bare /pseudo, which is how the reader tests it
    if feature.type == "CDS":
        qualifiers["transl_table"] = [str(feature.translation_table)]
    _add_attributes(qualifiers, feature)
    return qualifiers


def _add_attributes(qualifiers, feature):
    for key, values in feature.attributes.items():
        if key not in qualifiers:
            # An empty value is a bare qualifier (`/ribosomal_slippage`), which Biopython
            # writes for None and not for "".
            qualifiers[key] = [value if value != "" else None for value in values]


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

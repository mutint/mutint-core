"""Reading breseq reference files and deriving the artifacts the store keeps.

Two things live here:

* a minimal GFF3 reader, because Biopython has no GFF3 parser -- ``Bio.SeqIO`` covers
  GenBank and FASTA but GFF support lives in the separate ``bcbio-gff`` package. breseq's
  ``data/reference.gff3`` is machine-generated and regular, and GFF3 plus its ``##FASTA``
  section is far simpler to parse than GenBank, so a small reader is the better trade.
* a pure-Python ``.fai`` writer, so indexing a FASTA needs no samtools/pysam.

GenBank input is handled by ``mutint_import.annotate``, which normalization delegates to.
"""

import collections
import hashlib
import io
import os

from mutint_import import sniff
from mutint_import.annotate import gff3 as annotate_gff3
from mutint_import.annotate import loader as annotate_loader

FASTA_DIRECTIVE = "##FASTA"


def parse_fasta(handle):
    """Yield ``(seq_id, sequence)`` from an open text FASTA handle.

    The id is the first whitespace-delimited token of the header, matching how samtools and
    breseq name sequences.
    """
    seq_id = None
    chunks = []
    for line in handle:
        line = line.rstrip("\n").rstrip("\r")
        if line.startswith(">"):
            if seq_id is not None:
                yield seq_id, "".join(chunks)
            seq_id = line[1:].split()[0] if line[1:].strip() else ""
            chunks = []
        elif line:
            chunks.append(line.strip())
    if seq_id is not None:
        yield seq_id, "".join(chunks)


def read_gff3(path):
    """Return ``{"features": [...], "sequences": [(seq_id, sequence), ...]}``.

    Only what the store and the reference check need: feature rows and the trailing
    ``##FASTA`` block. breseq always writes the sequence inline, which is what lets the
    reference be validated without a separate FASTA upload.
    """
    features = []
    fasta_lines = []
    in_fasta = False

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.rstrip("\n").rstrip("\r")
            if not in_fasta and stripped.strip().upper() == FASTA_DIRECTIVE:
                in_fasta = True
                continue
            if in_fasta:
                fasta_lines.append(stripped)
                continue
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split("\t")
            if len(parts) < 9:
                continue
            features.append({
                "seq_id": parts[0],
                "source": parts[1],
                "type": parts[2],
                "start": _int_or_none(parts[3]),
                "end": _int_or_none(parts[4]),
                "strand": parts[6],
                "attributes": _parse_gff_attributes(parts[8]),
            })

    sequences = list(parse_fasta(io.StringIO("\n".join(fasta_lines)))) if fasta_lines else []
    return {"features": features, "sequences": sequences}


def write_fai(fasta_path, fai_path=None):
    """Write a samtools-compatible ``.fai`` for ``fasta_path``, in pure Python.

    Columns are name, length, offset of the first base, bases per line, and bytes per line.
    Reads the file in binary and tracks byte offsets directly, so it stays correct for CRLF
    files where a character count would drift.

    Raises ValueError if a record has ragged line lengths, which .fai cannot represent --
    the same condition samtools faidx rejects.
    """
    fai_path = fai_path or (fasta_path + ".fai")
    entries = []

    with open(fasta_path, "rb") as handle:
        name = None
        length = 0
        offset = 0
        line_bases = 0
        line_width = 0
        ragged = False
        position = 0

        for raw in handle:
            line_len = len(raw)
            stripped = raw.rstrip(b"\n").rstrip(b"\r")
            if raw.startswith(b">"):
                if name is not None:
                    entries.append((name, length, offset, line_bases, line_width))
                header = stripped[1:].decode("utf-8", "replace").strip()
                name = header.split()[0] if header else ""
                length = 0
                offset = position + line_len
                line_bases = 0
                line_width = 0
                ragged = False
            elif stripped:
                if line_bases == 0:
                    line_bases = len(stripped)
                    line_width = line_len
                elif len(stripped) > line_bases or ragged:
                    raise ValueError(
                        "FASTA record %r has ragged line lengths; cannot index" % (name,))
                elif len(stripped) < line_bases:
                    # A short line is only legal as the final line of a record.
                    ragged = True
                length += len(stripped)
            position += line_len

        if name is not None:
            entries.append((name, length, offset, line_bases, line_width))

    with open(fai_path, "w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write("%s\t%d\t%d\t%d\t%d\n" % entry)
    return fai_path


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_gff_attributes(field):
    attributes = {}
    for item in field.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        attributes[key.strip()] = value.strip()
    return attributes


# --- normalization ------------------------------------------------------------------
#
# A reference can arrive as GenBank, GFF3, or bare FASTA, and the same genome can be
# expressed differently by each -- and by different breseq versions. Everything is therefore
# converted to one canonical form before it is stored or hashed: breseq's own GFF3
# dialect, with an inline FASTA. Without that, two spellings of the same reference would
# hash differently and the "all samples share a reference" check would reject valid data.
#
# breseq's dialect rather than a reduced one because the stored file has three jobs: it is
# what the hash guards, what igv.js draws, and what annotation reads. Only the last needs
# the CDS/tRNA distinction, translation tables, pseudogene flags, spliced locations and
# repeat regions -- and without them annotation cannot compute a single codon.

FORMAT_GENBANK = "genbank"
FORMAT_GFF3 = "gff3"
FORMAT_FASTA = "fasta"

NORMALIZED_LINE_LENGTH = 70


class ReferenceFormatError(Exception):
    """The uploaded reference could not be read as GenBank, GFF3, or FASTA."""


_KIND_TO_FORMAT = {
    sniff.KIND_GENBANK: FORMAT_GENBANK,
    sniff.KIND_GFF3: FORMAT_GFF3,
    sniff.KIND_FASTA: FORMAT_FASTA,
}


def detect_format(path, original_name=None):
    """Identify the reference format from its name, falling back to a content sniff.

    A GenomeDiff is refused by name here rather than left to fail in a parser. It is the
    one wrong file people actually drop on this type -- it is the other thing an experiment
    is made of -- and "No GenBank records found" says nothing about what to do instead.
    Content decides that case even when the extension disagrees, because a `.gd` saved as
    `.gbk` is the same mistake with a worse error.
    """
    display_name = original_name or os.path.basename(path)
    kind = sniff.kind_of_file(path)
    if kind == sniff.KIND_GENOMEDIFF:
        raise ReferenceFormatError(
            "%s is a GenomeDiff (.gd) file, not a reference genome. A .gd holds mutations "
            "called against a reference and carries no sequence of its own -- import it "
            "from the Genome Diff tab, once the experiment has a reference."
            % (display_name,))

    name = display_name.lower()
    if name.endswith((".gbk", ".gb", ".genbank", ".gbff")):
        return FORMAT_GENBANK
    if name.endswith((".gff", ".gff3")):
        return FORMAT_GFF3
    if name.endswith((".fa", ".fasta", ".fna", ".fas")):
        return FORMAT_FASTA

    if kind in _KIND_TO_FORMAT:
        return _KIND_TO_FORMAT[kind]
    raise ReferenceFormatError(
        "%s is not recognizable as GenBank, GFF3, or FASTA: its first line begins none of "
        "them (a GenBank starts LOCUS, a GFF3 ##gff-version, a FASTA >)." % (display_name,))


def normalize_reference(path, original_name=None):
    """Return ``(gff3_text, sequences)`` in canonical form.

    ``sequences`` is ``[(seq_id, uppercase_sequence), ...]``; ``gff3_text`` is
    breseq-dialect GFF3 with an inline ``##FASTA`` block, so the pair is
    self-describing.

    The canonical form is breseq's own GFF3 rather than a reduced one, because the
    stored file has three jobs: it is what the shared-reference check hashes, what
    igv.js draws as the gene track, and what annotation reads. A reduced form can
    do the first two but not the third -- flattening a spliced gene or dropping the
    CDS/tRNA distinction leaves nothing to compute a codon from.

    It is produced by loading the reference into the annotation feature model and
    rendering it back out, so both input formats go through exactly one code path
    and a GenBank and breseq's GFF3 of the same genome necessarily agree.
    """
    fmt = detect_format(path, original_name)

    if fmt == FORMAT_FASTA:
        # No features to model; a bare FASTA is sequence only.
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            sequences = [(seq_id, sequence.upper())
                         for seq_id, sequence in parse_fasta(handle)]
        if not sequences:
            raise ReferenceFormatError(
                "%s carries no sequence" % (original_name or os.path.basename(path),))
        return _render_sequence_only_gff3(sequences), sequences

    try:
        references = annotate_loader.load_reference(path, original_name=original_name)
    except (annotate_loader.UnsupportedReferenceFormat, ValueError) as error:
        raise ReferenceFormatError(str(error))

    sequences = _sequences_of(references)
    if not sequences:
        raise ReferenceFormatError(
            "%s carries no sequence; a GFF3 needs an inline ##FASTA section"
            % (original_name or os.path.basename(path),))

    return annotate_gff3.render_breseq_gff3(references), sequences


def _sequences_of(references):
    """``[(seq_id, sequence), ...]`` for the distinct contigs, in a stable order."""
    contigs = []
    seen = set()
    for seq_id in sorted(references.seq_ids()):
        contig = references[seq_id]
        if id(contig) in seen:
            continue  # contigs are registered under several aliases
        seen.add(id(contig))
        contigs.append(contig)
    contigs.sort(key=lambda contig: contig.seq_id)
    return [(contig.seq_id, contig.sequence) for contig in contigs]


def _render_sequence_only_gff3(sequences):
    """A reference with no annotation at all: headers plus the sequence."""
    lines = ["##gff-version 3"]
    for seq_id, sequence in sequences:
        lines.append("##sequence-region\t%s\t1\t%d" % (seq_id, len(sequence)))
    lines.append("##FASTA")
    lines.append(render_fasta(sequences).rstrip("\n"))
    return "\n".join(lines) + "\n"


def sequence_digest(sequence):
    """sha256 of one sequence's bases alone -- no name, no wrapping, no case.

    This is what makes a genome a genome. `render_fasta` wraps the bases in `>seq_id`
    headers, so hashing *that* made two files describing the identical genome under
    different contig names hash differently and be rejected as different genomes.

    `.upper()` here rather than trusting the caller: the three loaders that build
    `sequences` all uppercase already, but an identity function must not depend on an
    invariant maintained somewhere else.
    """
    return hashlib.sha256(sequence.upper().encode("utf-8")).hexdigest()


def sequence_set_digest(sequences):
    """Name- and order-independent identity for ``[(seq_id, sequence), ...]``.

    A sorted *list* of the per-sequence digests, not a set: two identical contigs are two
    contigs, and a set would make a genome carrying one of them the same reference as a
    genome carrying both.

    Sorting also settles an asymmetry that predates this: the GenBank/GFF3 path sorts
    contigs by seq_id (`_sequences_of`) while the bare-FASTA path keeps file order, so the
    same genome could hash two ways depending on which format it arrived in.
    """
    joined = "".join(sorted(sequence_digest(sequence) for _seq_id, sequence in sequences))
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


def sequence_entries(sequences):
    """The `ReferenceSequences.seq_ids` value for `sequences`.

    One place, because it is written from two paths in `reference_store` and a rename
    rewrites it a third way -- and the per-sequence hash it carries is what a rename maps
    old names onto new ones by.
    """
    return [{"id": seq_id, "length": len(sequence),
             "sha256": sequence_digest(sequence)}
            for seq_id, sequence in sequences]


def render_fasta(sequences):
    """Canonical FASTA text: uppercase, fixed line length, LF endings.

    The hashed form -- two files describing the same genome must render identically here or
    the shared-reference check would reject them.
    """
    lines = []
    for seq_id, sequence in sequences:
        lines.append(">%s" % seq_id)
        for offset in range(0, len(sequence), NORMALIZED_LINE_LENGTH):
            lines.append(sequence[offset:offset + NORMALIZED_LINE_LENGTH])
    return "\n".join(lines) + "\n"


# --- several files, one reference --------------------------------------------------------
#
# A reference is often more than one file: the chromosome in one GenBank, a plasmid or a
# synthetic construct in another. The store keeps exactly one canonical pair per experiment,
# so the files are merged *here*, before anything is hashed or written -- and merged at the
# level of the loaded annotation model rather than by concatenating text, so that two files
# render to precisely the bytes one file holding both records would. The shared-reference
# hash must not depend on how a genome happened to be split up.


DuplicateContig = collections.namedtuple(
    "DuplicateContig", "skipped_id skipped_file kept_id kept_file")
DuplicateContig.__str__ = lambda self: (
    "contig %s in %s duplicates %s in %s and was ignored" % self)


def load_references(files):
    """Load ``[(path, original_name), ...]`` into one ``LoadedReferenceSequences``.

    Returns ``(references, duplicates)``, the second a list of ``DuplicateContig``. Each file is loaded on its own -- a bare FASTA into
    feature-less contigs, GenBank and GFF3 through the annotation loader -- so formats may be
    mixed across files even though one load of the annotation loader refuses to mix them.

    Two rules govern the merge, and they answer different mistakes:

    * **The same bases arriving twice is a duplicate, not a second contig.** A chromosome
      uploaded as a GenBank and again as a FASTA is one contig; the copy that carries
      annotation is kept when only one does, and otherwise the earlier file's. The skip is
      returned as a ``DuplicateContig`` so the caller can say so. This is deliberately only *across*
      files: within one file two identical contigs stay two contigs (the multiset rule in
      ``sequence_set_digest``), because a single file is one author's statement of the genome.
    * **One name for two different sequences is refused**, naming both files. Silently keeping
      either would hide exactly the mistake worth surfacing.
    """
    from mutint_import.annotate.model import LoadedReferenceSequences

    merged = LoadedReferenceSequences()
    # sha256 of the bases -> (contig, file it came from); how a duplicate is recognized.
    by_digest = {}
    # every key registered on `merged` -> file it came from; how a name clash is described.
    key_owner = {}
    duplicates = []

    for path, original_name in files:
        display_name = original_name or os.path.basename(path)
        loaded = _load_one(path, display_name)
        # Indexed only once the whole file is in, so two identical contigs *within* a file
        # are never read as one duplicating the other.
        this_file = []
        for contig, keys in _distinct_contigs(loaded):
            found = by_digest.get(sequence_digest(contig.sequence))
            if found is not None:
                kept, kept_file = found
                if contig.features and not kept.features:
                    # The annotated copy wins whatever order the files came in: dropping it
                    # would establish a genome with no genes while a file full of them sat
                    # in the same drop.
                    _forget(merged, key_owner, kept)
                    duplicates.append(DuplicateContig(
                        kept.seq_id, kept_file, contig.seq_id, display_name))
                else:
                    duplicates.append(DuplicateContig(
                        contig.seq_id, display_name, kept.seq_id, kept_file))
                    continue
            for key in keys:
                if key in key_owner:
                    raise ReferenceFormatError(
                        "%s and %s both define contig %s with different sequences; every "
                        "contig may be named once across the files of one reference"
                        % (key_owner[key], display_name, key))
            for key in keys:
                merged.sequences[key] = contig
                key_owner[key] = display_name
            this_file.append(contig)
        for contig in this_file:
            by_digest[sequence_digest(contig.sequence)] = (contig, display_name)

    return merged, duplicates


def normalize_references(files):
    """``[(path, original_name), ...]`` -> ``(gff3_text, sequences, duplicates)``.

    The several-file form of ``normalize_reference``: the genome the files make up together,
    in the same canonical form. ``duplicates`` lists every contig skipped as a duplicate of
    one in another file, as ``DuplicateContig`` (see ``load_references``).

    One file delegates to ``normalize_reference`` outright, so a single file normalizes to
    exactly the bytes it always has -- a multi-record bare FASTA keeps its file order there,
    where the merge below sorts, and there is no reason to churn stored hashes for that.
    """
    files = list(files)
    if len(files) == 1:
        path, original_name = files[0]
        gff3_text, sequences = normalize_reference(path, original_name)
        return gff3_text, sequences, []

    references, duplicates = load_references(files)
    sequences = _sequences_of(references)
    if not sequences:
        raise ReferenceFormatError("the files carry no sequence")
    return annotate_gff3.render_breseq_gff3(references), sequences, duplicates


def _load_one(path, display_name):
    """One file as a ``LoadedReferenceSequences``, whatever its format."""
    from mutint_import.annotate.model import AnnotatedSequence, LoadedReferenceSequences

    fmt = detect_format(path, display_name)
    if fmt != FORMAT_FASTA:
        try:
            return annotate_loader.load_reference(path, original_name=display_name)
        except (annotate_loader.UnsupportedReferenceFormat, ValueError) as error:
            raise ReferenceFormatError(str(error))

    references = LoadedReferenceSequences()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for seq_id, sequence in parse_fasta(handle):
            contig = AnnotatedSequence(seq_id, sequence.upper())
            contig.update_feature_lists()
            references.add(contig)
    if not references.sequences:
        raise ReferenceFormatError("%s carries no sequence" % (display_name,))
    return references


def _distinct_contigs(references):
    """``[(contig, [every key it is registered under]), ...]`` in file order.

    A GenBank contig is registered under its LOCUS name and, as an alias, its VERSION
    accession; both have to travel with it, and neither may count as a second contig.
    """
    keys_of = {}
    order = []
    for key, contig in references.sequences.items():
        if id(contig) not in keys_of:
            keys_of[id(contig)] = []
            order.append(contig)
        keys_of[id(contig)].append(key)
    return [(contig, keys_of[id(contig)]) for contig in order]


def _forget(references, key_owner, contig):
    """Unregister `contig` from `references` under every key it holds."""
    for key in [k for k, c in references.sequences.items() if c is contig]:
        del references.sequences[key]
        key_owner.pop(key, None)


"""Reading breseq reference files and deriving the artifacts the store keeps.

Two things live here:

* a minimal GFF3 reader, because Biopython has no GFF3 parser -- ``Bio.SeqIO`` covers
  GenBank and FASTA but GFF support lives in the separate ``bcbio-gff`` package. breseq's
  ``data/reference.gff3`` is machine-generated and regular, and GFF3 plus its ``##FASTA``
  section is far simpler to parse than GenBank, so a small reader is the better trade.
* a pure-Python ``.fai`` writer, so indexing a FASTA needs no samtools/pysam.

GenBank input is handled by ``aledb_import.annotate``, which normalization delegates to.
"""

import hashlib
import io
import os

from aledb_import.annotate import gff3 as annotate_gff3
from aledb_import.annotate import loader as annotate_loader

FASTA_DIRECTIVE = "##FASTA"


def sha256_file(path, _chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_chunk), b""):
            digest.update(block)
    return digest.hexdigest()


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


def write_fasta(sequences, path, line_length=70):
    """Write ``[(seq_id, sequence), ...]`` as FASTA with a fixed line length."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for seq_id, sequence in sequences:
            handle.write(">%s\n" % seq_id)
            for offset in range(0, len(sequence), line_length):
                handle.write(sequence[offset:offset + line_length] + "\n")
    return path


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


def detect_format(path, original_name=None):
    """Identify the reference format from its name, falling back to a content sniff."""
    name = (original_name or os.path.basename(path)).lower()
    if name.endswith((".gbk", ".gb", ".genbank", ".gbff")):
        return FORMAT_GENBANK
    if name.endswith((".gff", ".gff3")):
        return FORMAT_GFF3
    if name.endswith((".fa", ".fasta", ".fna", ".fas")):
        return FORMAT_FASTA

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(">"):
                return FORMAT_FASTA
            if stripped.startswith("##gff-version") or stripped.startswith("##sequence-region"):
                return FORMAT_GFF3
            if stripped.startswith("LOCUS"):
                return FORMAT_GENBANK
            break
    raise ReferenceFormatError(
        "%s is not recognisable as GenBank, GFF3, or FASTA" % (original_name or path,))


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

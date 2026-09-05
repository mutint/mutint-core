"""What a dropped file actually is, read from its first line.

The Add page routes a drop on the type you chose and the suffixes that type claims, which is
right up until the two disagree with the file itself -- a `.gd` chosen as a reference genome,
or a GenBank saved as `.gd`. What came back then was the parser's own words ("No GenBank
records found in: ..."), which name what failed to happen rather than what to do about it.

All four formats declare themselves on their first non-blank line, so one line settles it.
Deliberately only that: this is for telling a user they picked the wrong type, not for
guessing types on their behalf. `detect_format` still trusts the extension where the two
agree, and auto-detect still routes on `patterns`.
"""

KIND_GENOMEDIFF = "genomediff"
KIND_GENBANK = "genbank"
KIND_GFF3 = "gff3"
KIND_FASTA = "fasta"

# Phrased to drop into "<file> is ...", so each reads as a sentence wherever it is used.
KIND_DESCRIPTIONS = {
    KIND_GENOMEDIFF: "a GenomeDiff (.gd) file",
    KIND_GENBANK: "a GenBank file",
    KIND_GFF3: "a GFF3 file",
    KIND_FASTA: "a FASTA file",
}

# Order matters: a GenomeDiff and a GFF3 both open with '#'.
_MARKERS = (
    ("#=GENOME_DIFF", KIND_GENOMEDIFF),
    ("##gff-version", KIND_GFF3),
    ("##sequence-region", KIND_GFF3),
    ("LOCUS", KIND_GENBANK),
    (">", KIND_FASTA),
)


def kind_of_text(text):
    """The format `text` declares itself to be, or None if its first line says nothing."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for marker, kind in _MARKERS:
            if stripped.startswith(marker):
                return kind
        # Only the first line that carries anything: a file whose opening line declares
        # nothing is not going to declare itself further down, and reading on would start
        # guessing.
        return None
    return None


def kind_of_file(path):
    """As `kind_of_text`, reading only as far as the first non-blank line."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    return kind_of_text(line)
    except OSError:
        return None
    return None


def describe(kind):
    """The phrase for `kind`, for messages that say what a file turned out to be."""
    return KIND_DESCRIPTIONS.get(kind, "an unrecognized file")

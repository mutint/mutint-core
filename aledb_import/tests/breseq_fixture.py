"""Build synthetic breseq result folders for tests.

Written on the fly rather than committed, so the suite carries no binary fixtures. The BAM
and BAI are arbitrary bytes: nothing in this codebase parses them — they are copied into the
store and served back byte for byte — so their contents only need to be stable and
recognisable, not valid BGZF.
"""

import os

from aledb_import.breseq_folder import (
    BAI_RELATIVE_PATH,
    BAM_RELATIVE_PATH,
    FASTA_RELATIVE_PATH,
    GD_RELATIVE_PATH,
    GFF3_RELATIVE_PATH,
)

GD_TEXT = """#=GENOME_DIFF\t1.0
#=REFSEQ\ttest_ref
SNP\t1\t.\ttest_ref\t100\tA\tgene_name=thrA\tgene_product=aspartokinase\tfrequency=1
DEL\t2\t.\ttest_ref\t200\t5\tgene_name=thrB/thrC\tgene_product=x/y\tfrequency=0.5
"""

SEQUENCE_A = "ACGT" * 40      # 160 bases
SEQUENCE_B = "GGTTAACC" * 10  # 80 bases


def fasta_text(sequences, line_length=70):
    out = []
    for seq_id, sequence in sequences:
        out.append(">%s" % seq_id)
        for offset in range(0, len(sequence), line_length):
            out.append(sequence[offset:offset + line_length])
    return "\n".join(out) + "\n"


def gff3_text(sequences):
    lines = ["##gff-version 3"]
    for seq_id, sequence in sequences:
        lines.append("##sequence-region\t%s\t1\t%d" % (seq_id, len(sequence)))
    for seq_id, _sequence in sequences:
        lines.append("\t".join([
            seq_id, "breseq", "gene", "50", "150", ".", "+", ".",
            "ID=gene1;Name=thrA;product=aspartokinase"]))
    lines.append("##FASTA")
    lines.append(fasta_text(sequences).rstrip("\n"))
    return "\n".join(lines) + "\n"


def write_sample(root, sample_name, sequences=None, gd_text=None,
                 include_bai=True, gff3_override=None, fasta_override=None,
                 bam_bytes=None, gd_relative_path=None):
    """Create one breseq sample folder under ``root``; returns its path.

    ``gd_relative_path`` writes the mutations somewhere other than breseq's
    ``output/output.gd`` -- used to cover the ``output/annotated.gd`` fallback.
    """
    sequences = sequences or [("test_ref", SEQUENCE_A)]
    sample_dir = os.path.join(root, sample_name)

    for relative, content in (
        (gd_relative_path or GD_RELATIVE_PATH,
         gd_text if gd_text is not None else GD_TEXT),
        (GFF3_RELATIVE_PATH,
         gff3_override if gff3_override is not None else gff3_text(sequences)),
        (FASTA_RELATIVE_PATH,
         fasta_override if fasta_override is not None else fasta_text(sequences)),
    ):
        path = os.path.join(sample_dir, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)

    bam_path = os.path.join(sample_dir, BAM_RELATIVE_PATH)
    os.makedirs(os.path.dirname(bam_path), exist_ok=True)
    with open(bam_path, "wb") as handle:
        handle.write(bam_bytes if bam_bytes is not None
                     else (b"BAM\x01" + bytes(range(256)) * 4))

    if include_bai:
        with open(os.path.join(sample_dir, BAI_RELATIVE_PATH), "wb") as handle:
            handle.write(b"BAI\x01" + b"\xff" * 64)

    return sample_dir

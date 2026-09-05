# Reference genomes

**Every sample in an experiment shares one reference genome.** The first import establishes it;
a later sample whose reference does not match is rejected on its own while the rest of the
batch imports.

There is no separate page for this. A GenBank, GFF3 or FASTA dropped alongside `.gd` files in
one drop is established first, whatever order the files are listed in, because the reference
import type runs at a higher priority than anything checked against it.

## Sequence is the sole invariant

Two samples belong to the same experiment when their reference **sequence** is identical.
Annotation may legitimately differ in detail between breseq runs, and that alone never causes a
rejection.

A folder import never rewrites the experiment's annotation — import order should not decide it.
**Replace annotation** is the import type that does, because that is an explicit request. It
claims GenBank and GFF3 but not FASTA: a FASTA is sequence with no features, so there is
nothing in one to install.

## Normalization

Whatever arrives is converted to one canonical pair before being stored or hashed — a GFF3 of
genes only, plus a FASTA. Without that step a GenBank and the GFF3 breseq derived from the same
genome would hash differently and the shared-reference check would reject perfectly good data.

!!! note "Contig names come from LOCUS, not VERSION"

    A GenBank record's `seq_id` is taken from its **LOCUS** line (`NC_000913`), not its
    VERSION (`NC_000913.3`). breseq does the same, so every `seq_id` in a `.gd` it writes is
    the unversioned one — and reference lookups match names exactly, on purpose. Taking
    VERSION would make a GenBank and breseq's own GFF3 of the same genome disagree about what
    its contigs are called.

    The versioned accession is kept as an alias, so a lookup by either spelling resolves.

## Annotation

Gene, codon and amino-acid fields are **derived at import** from the stored reference, not read
out of the `.gd`. breseq's plain `output.gd` is enough; `gdtools ANNOTATE` is not needed.

A mutation imported before a reference existed has no annotation and renders through a plainer
fallback. Nothing is lost — the verbatim record is kept — and `./mutint reannotate <id>`
recomputes annotations in place once a reference arrives. Re-importing is not needed.

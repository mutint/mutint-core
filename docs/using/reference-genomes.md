# Reference genomes

**Every sample in an experiment shares one reference genome.** The first import establishes it;
a later sample whose reference does not match is rejected on its own while the rest of the
batch imports.

There is no separate page for this. A GenBank, GFF3 or FASTA dropped alongside `.gd` files in
one drop is established first, whatever order the files are listed in, because the reference
import type runs at a higher priority than anything checked against it.

## A reference may be several files

A chromosome in one GenBank and a plasmid or a synthetic construct in another, dropped
together on the **Reference Sequence** tab, are one reference. The files are combined before
anything is stored or hashed, so a genome split across files is indistinguishable from the
same genome in one file. Formats may be mixed: a GenBank chromosome and a FASTA plasmid is
fine. From the shell, put the files in one directory and import that.

Three rules apply across the files of one drop:

- **The same sequence in two files is one contig.** A chromosome uploaded as a GenBank and
  again as a FASTA is not two chromosomes; the copy carrying annotation is kept, and the
  skipped one is reported under its file's row. Within a single file two identical contigs
  stay two contigs -- one file is one statement of the genome.
- **One name for two different sequences is refused**, naming both files.
- **A file that cannot be read stops the whole set**, and nothing is established. A reference
  missing its plasmid could not be completed from this page afterwards, since the tab is
  withdrawn once the experiment has a reference and *Replace annotation* refuses a different
  sequence.

**Replace annotation** takes several files the same way -- and, as with one file, the files
together must carry the whole genome.

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

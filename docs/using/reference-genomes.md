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

## From an NCBI accession

The **Reference Sequence** tab takes accessions as well as files. Type them into the box above
the drop zone, separated by commas, spaces, semicolons or new lines:

- **Nucleotide accessions** — `NC_000913.3`, `U00096.3`, `CP009273.1`. An unversioned
  accession is resolved to whatever version NCBI currently holds, and that is the record you
  get.
- **Genome assembly accessions** — `GCF_000005845.2`, `GCA_000005845.2`. The assembly is
  resolved to the nucleotide records it is made of and **every sequence it lists** is
  downloaded: chromosome, plasmids and unplaced scaffolds alike. A `GCF_` accession takes each
  sequence's RefSeq spelling and a `GCA_` its GenBank one.

**Accessions and dropped files are one import.** A chromosome typed as an accession and a
plasmid dropped as a FASTA make a single reference, on the same terms as two dropped files —
see *A reference may be several files* above. The download lands in the same staging area the
files do, and everything after that is identical.

**A typo is refused before anything is uploaded.** Each accession is looked up when you press
Import, and an accession NCBI has no record of stops the import there, naming it and saying
which database was searched. Nothing is staged and nothing has to be dropped again.

**Update Annotation takes accessions too**, which is the usual way to move an established
genome onto a newer annotation of the same sequence.

### The NCBI link is recorded for you

A contig that arrived this way is linked to the record it came from, and the **Reference** page
shows it as confirmed with nobody having pressed *Check*. That is not a shortcut around the
check that page offers: the bases came out of that record, which is stronger evidence than the
accession somebody types beside a sequence that arrived some other way. Mutations on such a
contig can be drawn in NCBI's own annotation immediately.

The contig is still **named for its LOCUS line** (`NC_000913`), while the link is filed under
the **VERSION** (`NC_000913.3`) — see the note under *Normalization*. The two are tied together
by the sequence itself rather than by either name.

### What it needs, and what it costs

Outbound access to `eutils.ncbi.nlm.nih.gov` and `api.ncbi.nlm.nih.gov`. A deployment with no
outbound network cannot use this way in, and nothing else about it changes.

`MUTINT_NCBI_API_KEY` and `MUTINT_NCBI_EMAIL` are optional (see *Configuration*). Neither is
required, and both are **the operator's, used for every user's request** — an API key raises
NCBI's rate limit for this deployment from 3 requests a second to 10, and identifies the
deployment to NCBI. Requests within one import are spaced so that a single import stays under
the slower limit either way; two people importing at the same moment can still be rate-limited,
which is reported as such and costs nothing but the retry.

Imports are capped by `MUTINT_NCBI_MAX_BASES` (50 Mb by default) across the whole drop, and at
500 sequences per accession.

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

### Updating the annotation re-annotates everything

Dropping a better annotation of the same sequence on the **Update Annotation** tab replaces
the stored annotation and re-derives every mutation's gene, codon and amino-acid fields
against it, then recomputes what depends on them. Nothing needs re-importing and
`./mutint reannotate` is not needed afterwards.

### Reference annotators

Installed components can offer tools that run *on* the reference after it lands — ISEScan,
which predicts insertion sequences, is the first. Each appears as a panel on the Reference
Sequence and Update Annotation tabs with a box to tick; ticked, it runs after the reference is
set or updated. On the Update Annotation tab, **Run annotators** runs the ticked ones against
the current reference with nothing uploaded. What each one does is described on its panel and
in its component's own pages.

## Downloading the reference

The experiment's **Reference** page lists every sequence in the stored reference, and each
row has a checkbox. Tick the ones you want, choose a format from the menu above the table
and press **Download**. Everything is ticked to begin with, so one click gets the whole
reference.

Three formats are offered:

| format | what you get |
|---|---|
| **FASTA** | the sequence alone, one record per contig |
| **GFF3** | breseq's own dialect, gene and repeat features with the sequence inline under `##FASTA` — the form the store keeps, and the one breseq and `./mutint reannotate` read directly |
| **GenBank** | the same features as GenBank records, one per contig |

What every format carries is the **stored** reference, described under *Normalization*
above: the sequence, and the CDS, RNA and repeat features breseq annotates against, with
their names, locus tags and products. The file the reference was imported from is not kept,
so a GenBank download is generated from that, not returned from it. It has no `/translation`,
no `source` feature and no organism, and its date is a placeholder.

The download is a plain address, so a script can fetch it with the same access a browser has:

```
/mutations/reference/<experiment id>/download?format=genbank&seq_id=NC_000913&seq_id=pXYZ
```

`format` is `fasta`, `gff3` or `genbank`, and leaving out every `seq_id` means every contig.

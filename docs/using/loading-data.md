# Loading data

Everything arrives through **one page** — `/import/add/?experiment_id=<id>` — or the
equivalent command. There is no separate reference-genome page and no per-format workflow: you
drop files, and the type is either chosen or detected.

Every path ends in the same importer, so a CLI upload and a browser drop produce identical
rows.

## From the command line

```bash
./aledb import /path/to/experiment1 /path/to/experiment2 \
    --project "My project" --experiment "My experiment" --person alice
./aledb delete 4 20 19
```

Each path is the root of a breseq output directory — the one holding `output/` and `data/`.
Deleting is **soft**: rows are flagged, and `./aledb purge_deleted --older-than <days>` is what
finally removes them.

## From the browser

Drop breseq result folders on the Add Data page. Five files per sample are uploaded and
nothing else, so the bulk of a run never leaves your machine:

```
<sample>/data/output.gd
<sample>/data/reference.gff3    <sample>/data/reference.fasta
<sample>/data/reference.bam     <sample>/data/reference.bam.bai
```

Large folders upload in chunks with progress, so a multi-gigabyte drop never rides on one long
request. A bare `.gd` works too — it just carries no reads, so that sample has mutation calls
and no alignment to look at.

**Sample statistics come from `data/summary.json`** — total reads, average read length, percent
mapped, mean coverage. A sample without that file still imports with those left at zero.

## Where a sample sits in the evolution

Identity comes from the **filename**, and two shapes are read:

```
3-30000-1-1.gd          ALE 3,     flask 30000, isolate 1,    replicate 1
Ara-2_500gen_763A.gd    ALE Ara-2, flask 500,   isolate 763A, replicate 1
```

The first is four whole numbers separated by dashes. The second is three fields separated by
underscores: a lineage name, a time point, and an isolate name. **The ALE and the isolate are
kept exactly as written** — `Ara-1` and `Ara+1` are two different populations, and `763A` and
`763B` two different clones from one flask, so nothing is stripped off either. Only the middle
field is read as a number, because a time point is one: `500gen` is flask 500. A middle field
that does not start with a digit (`t0`) is not a time point, and the name falls through to
auto-numbering.

Anything else — `REL606_clone.gd` — gets its own auto-numbered isolate under ALE 1, flask 1,
with the filename kept as its description so it displays by name.

!!! warning "Auto-numbering has a consequence worth knowing before you import"

    A 51-timepoint series imported under names of neither shape becomes 51 isolates of a
    single flask. Analyses that read the *flask* as the time axis then have nothing to work
    with — the Fixed Mutations analysis intersects an ALE's last two flasks, so an ALE with
    one flask can never fix anything.

    (Named rather than linked: a component's pages sit at a different path depending on
    whether it is being built alone or as part of a deployment's manual, so a link between
    components is broken in one of the two.)

    If your samples have a meaningful position in the evolution, use one of the two shapes
    above. Renaming files before a drop is cheaper than moving samples afterwards, though the
    sample editor can do that too.

## Storage

Files are stored under `ALEDB_STORE_DIR`, keyed by database id:

```
<store>/experiments/<experiment_id>/reference/{reference.gff3,reference.fasta,reference.fasta.fai}
<store>/samples/<sample_id>/{sample.gd,aligned.bam,aligned.bam.bai}
```

Alignments are served with HTTP range support, which is what lets the genome browser read them
without downloading. Every request is permission-checked like any other page.

## Looking at alignments

Click a frequency in a mutation table to open the genome browser at that position, showing the
read pileup for that sample. Other samples in the experiment can be added as tracks to compare
them at the same locus, and coverage is drawn from a stored BigWig so it survives at
whole-genome zoom where the reads do not.

Only a sample imported as a **breseq result folder** has an alignment; that is what carries
`reference.bam`. A sample from a bare `.gd` renders its frequencies as plain text.

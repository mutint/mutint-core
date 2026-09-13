# Loading data

Everything arrives through **one page** — `/import/?experiment_id=<id>` — or the
equivalent command. There is no separate reference-genome page and no per-format workflow: you
drop files, and the type is either chosen or detected.

Every path ends in the same importer, so a CLI upload and a browser drop produce identical
rows.

## From the command line

```bash
./mutint import /path/to/experiment1 /path/to/experiment2 \
    --project "My project" --experiment "My experiment" --owner alice
./mutint delete 4 20 19
```

Each path is the root of a breseq output directory — the one holding `output/` and `data/`.
Deleting is **soft**: rows are flagged, and `./mutint purge_deleted --older-than <days>` is what
finally removes them.

## From the browser

Drop breseq result folders on the Import data page. Five files per sample are uploaded and
nothing else, so the bulk of a run never leaves your machine:

```
<sample>/data/output.gd
<sample>/data/reference.gff3    <sample>/data/reference.fasta
<sample>/data/reference.bam     <sample>/data/reference.bam.bai
```

Large folders upload in chunks with progress, so a multi-gigabyte drop never rides on one long
request. A bare `.gd` works too — it just carries no reads, so that sample has mutation calls
and no alignment to look at.

Each drop is listed as you add it, with a **Remove** beside it, so a folder dragged by mistake
comes back out without touching the rest. **Reset** clears the whole page — every drop and the
chosen type — to how it loaded.

**Sample statistics come from `data/summary.json`** — total reads, average read length, percent
mapped, mean coverage. A sample without that file still imports with those left at zero.

## Where a sample sits in the evolution

A sample's place -- its population, its time point, and its name within the population --
can come from three sources, and the first that answers wins:

1. a **`metadata.csv`** dropped with the data (below);
2. the file's **own header**: `#=SAMPLE`, `#=POPULATION` and `#=TIME` in a `.gd`, or
   `##sample=`, `##population=` and `##generation=` in a VCF (see below for the synonyms);
3. the **filename**.

Whichever names it, the file's own name is kept as the sample's *source name*, which is what
a re-import matches on.

### Naming samples with metadata.csv

Drop a file called `metadata.csv` beside the data, on the Import data page or among read
files on the Run breseq page. It has a header row and then one row per sample:

```
sample,population,time_point,sample_type,data
763A,Ara-2,500,clone,Ara-2_500gen_763A.gd
ZDB16,Ara-3,30000,,ZDB16
A1,glucose,10,population,s1;s1_lane2
mystery,,,,mystery_sample.gd
```

- `sample` is the name within its population; `population` and `time_point` place it.
  Leave both blank for a sample that is not placed yet.
- `sample_type` is optional: `population` or `mixed` marks a sample of many genotypes,
  `clone`, `individual` or `isolate` one genotype. Left blank, or with the column absent,
  the data decides (breseq's `-p` in the `.gd`'s command line). On the Run breseq page it
  also sets the **Population sample** option for that sample's run.
- `data` names the inputs the row covers: a breseq results folder by its name, a `.gd` or
  VCF by its filename (the extension may be left off), or on the Run breseq page a read
  file's name or a stem its mates share (`s1` covers `s1_R1.fastq.gz` and `s1_R2.fastq.gz`).
  Several in one cell, separated by `;`, or the same row repeated.
- Lines beginning with `#` are ignored, so the file can carry notes.

The Import data page links to a blank template and a worked example under its drop zone. A
row that names nothing in the drop, and an input no row names, are reported after the
import and do not stop it; an input named by two rows is refused. A file that cannot be
read at all -- a missing column, say -- refuses the whole drop, because everything under it
would otherwise land unplaced. Importing the same data with the same file again changes
nothing; importing it with a *different* placement moves the sample, unless another sample
already holds that place, which is reported.

### Naming a sample inside the file

The same three fields can be written into the file's header, which suits data that travels:
a `.gd` with `#=POPULATION Ara-3` and `#=TIME 30000` places itself, as the LTEE's do. The
keys accept synonyms, matched without regard to case, spaces or underscores: `sample` or
`name`; `population`, `treatment` or `condition`; `time_point`, `time`, `generation` or
`transfer`; and `sample_type`, with the values the CSV column takes. In a VCF the same keys go on `##key=value` lines and apply to every sample
column in the file; a multi-sample file places columns separately with the spec's own
`##SAMPLE=<ID=col,population=Ara-2,generation=500>` lines. A header that names some fields
takes the rest from the filename, so `3-30000-1-1.gd` carrying only `#=POPULATION Ara-3`
lands at Ara-3, time point 30000, sample 1-1.

### Naming a sample by its filename

With neither of those, identity comes from the **filename**, and two shapes are read:

```
3-30000-1-1.gd          population 3,     time point 30000, sample 1,    replicate 1
Ara-2_500gen_763A.gd    population Ara-2, time point 500,   sample 763A
```

The first is four whole numbers separated by dashes. The second is three fields separated by
underscores: a population name, a time point, and a sample name. **The population and the sample are
kept exactly as written** — `Ara-1` and `Ara+1` are two different populations, and `763A` and
`763B` two different clones from one time point, so nothing is stripped off either. Only the middle
field is read as a number, because a time point is one: `500gen` is time point 500. A middle field
that does not start with a digit (`t0`) is not a time point, and the name falls through to
auto-numbering.

Anything else — `REL606_clone.gd` — gets its own sample under a population called
**Unspecified**, with **no time point**, and the filename kept as its description so it
displays by name. Both say plainly that nobody has placed the sample: an unspecified sample
sorts before the placed ones and is left out of anything that reads the time point as an axis.

!!! warning "Being unplaced has a consequence worth knowing before you import"

    A 51-timepoint series imported under names of neither shape becomes 51 samples with no
    time point at all. Analyses that read the *time point* as the time axis then have nothing
    to work with — the Fixed Mutations analysis intersects an ALE's last two time points, so a
    population whose samples have no time point can never fix anything.

    (Named rather than linked: a component's pages sit at a different path depending on
    whether it is being built alone or as part of a deployment's manual, so a link between
    components is broken in one of the two.)

    If your samples have a meaningful position in the evolution, use one of the two shapes
    above, or drop a `metadata.csv` beside the files saying where each goes. Either is
    cheaper than moving samples afterwards, though the sample editor can do that too.

## Storage

Files are stored under `MUTINT_STORE_DIR`, keyed by database id:

```
<store>/experiments/<experiment_id>/reference/{reference.gff3,reference.fasta,reference.fasta.fai}
<store>/samples/<sample_id>/{sample.gd,aligned.bam,aligned.bam.bai,coverage.bw}
<store>/samples/<sample_id>/report/...        breseq's own HTML report
```

Alignments are served with HTTP range support, which is what lets the genome browser read them
without downloading. Every request is permission-checked like any other page.

How much of this each experiment takes, and how to drop the alignments or the report once you
are done with them, is [Disk space](storage.md).

## Looking at alignments

Click a frequency in a mutation table to open the genome browser at that position, showing the
read pileup for that sample. Other samples in the experiment can be added as tracks to compare
them at the same locus, and coverage is drawn from a stored BigWig so it survives at
whole-genome zoom where the reads do not.

Only a sample imported as a **breseq result folder** has an alignment; that is what carries
`reference.bam`. A sample from a bare `.gd` renders its frequencies as plain text.

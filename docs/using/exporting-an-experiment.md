# Exporting an experiment

An experiment can leave one MutInt and arrive in another as a single zip: the reference
genome, every sample's mutations, and the details that no mutation file carries -- the
experiment's name and notes, each population's species and strain, each sample's flags and
description, the publications, and which sample is the ancestor.

## Getting the archive

**Export experiment** is on the experiment's **Reference** page, beside the reference
download, and the Import data page links to it under the tab strip. Either gives a zip named
for the experiment. Anyone who can view the experiment can take it.

On an installation that publishes the API, a public experiment's archive is also at
`/api/experiments/<id>/archive`, with no sign-in -- see *The API* for pulling one from a
script.

## What is inside

```
<experiment>/
  mutint.json                the manifest: the experiment, its populations and samples
  metadata.csv               where each sample sits, in the form any drop accepts
  reference/reference.gff3   the stored reference, exactly as this MutInt holds it
  reference/reference.fasta
  samples/<name>.gd          one file per sample
  samples/<name>.vcf         ... as VCF, for a sample that arrived as one
```

Each sample's file is the same export its own page offers, so it carries the sample's
placement in its header and can be dropped on its own if that is all you want. The archive
holds **mutations and the reference only**: read alignments, coverage tracks and breseq's
HTML report stay on the installation that made them. They are gigabytes, and every analysis
MutInt does reads the mutations.

## Importing it

Create an experiment on the other MutInt, open its Import data page and choose the **MutInt
Archive** tab. Drop the zip, or the folder you unzipped it into. The tab is offered whether
or not the experiment has a reference yet, because the archive brings one.

From a shell, `./mutint import <the folder or zip> --experiment-id <id>` does the same, and
so does dropping the folder on a new experiment with no type chosen: the archive is
recognised by its `mutint.json`.

What happens, in order:

1. The **reference** is established from the archive's copy. An experiment that already has
   a reference must have the *same* sequence; a different genome refuses the whole archive,
   since every mutation in it is defined against the one it came with. A newer annotation of
   the same sequence replaces the target's, the way the Update Annotation tab would.
2. Each **sample** is imported through the same path a `.gd` or VCF drop takes, placed by the
   archive's own `metadata.csv`. Importing the same archive twice lands on the same samples
   rather than making a second set.
3. The **manifest** is applied, and it is authoritative: the experiment's name and notes,
   the populations' descriptions, species and strains, every sample's flags, description and
   recorded details, the publications and the ancestor all replace whatever the target had.

Two things deliberately do not travel. A **lock** is a decision about one installation's
copy of the data, so the target is left unlocked for its owner to lock. And nothing about
the **project**: the archive lands in an experiment you created, inside a project you
already own, with whatever sharing that project has.

## Older and newer MutInts

The manifest carries a format version. A MutInt reading an archive written by a newer one
than it knows refuses it and says so, rather than importing what it half understands.

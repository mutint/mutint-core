# Disk space

MutInt keeps files as well as rows. Each sample imported from a breseq folder brings its
aligned reads, their index and a coverage track, and breseq's own HTML report with the
evidence behind every call. Those are most of what an experiment costs on disk, and once
its mutations are in the database none of them is needed by any table -- only the genome
browser reads the alignments and only the report viewer reads the report.

## Where the numbers are

- **The dashboard** has a *Stored Data* panel: the total across every live experiment, split
  by kind, and two lines for what that total leaves out -- experiments that were deleted and
  not yet purged, and files nothing in the database points at (see below).
- **The experiment and project lists** carry a *Stored data* column, sortable.
- **An experiment's Overview** has a *Storage* panel: the same kinds for that experiment,
  with a **Clear** button beside each kind that can be cleared.
- **A project's page** has the same table summed over its experiments, with *Clear in every
  experiment*.

Sizes are measured when files change -- after an import, after a coverage track is built,
after a clear -- and stored, so none of these pages walks the store to render. An experiment
that has never been measured shows as 0 in the lists until its Overview or the dashboard is
opened, which measures it; `./mutint rebuild --all --only storage` measures every experiment
from a shell, which is worth doing once after upgrading to a version that has this.

## What Clear removes, and what it never touches

Two kinds can be cleared:

| Kind | Files | What stops working |
|---|---|---|
| Alignments and coverage | `aligned.bam`, `aligned.bam.bai`, `coverage.bw` per sample | the genome browser for those samples; the frequency cells stop linking to it |
| breseq HTML report | the `report/` tree per sample | the *breseq report* link on the Mutations page |

They are one unit each: the coverage track is derived from the BAM and cannot be rebuilt
without it, so it goes with the reads. The samples, their mutations, the reference genome and
each sample's `.gd` file are never cleared -- they are the data. Clearing is not undoable;
re-importing the breseq folder brings the files back.

A **locked** experiment refuses, like every other write. Clearing from a project's page skips
its locked experiments and names them, and clears the rest.

A plugin may register a kind that is counted and not offered for clearing. mutint-breseq's
run directories are one: a failed run keeps its reads on purpose, and deleting the run on the
*Run breseq* page is how to free them.

## Deleted, purged, unattributed

Deleting an experiment in the browser flags it; `./mutint purge_deleted` removes it for real
after the retention window, files included. Until then its files are counted on the
dashboard as **awaiting purge**.

**Unattributed** is what no row owns: a drop abandoned in staging before
`./mutint reap_uploads` runs, and the directories `./mutint delete` leaves behind, since it
removes rows and touches no file. It is reported so the total on the dashboard is the whole
store; there is no button for it, because nothing in the database can say what it is.

## From the shell

```bash
./mutint storage --list                   # every live experiment, per kind, with totals
./mutint storage 4                        # one experiment
./mutint storage --clear alignments 4     # clear one kind for one experiment
```

`--clear` does not consult the experiment lock: the lock guards the browser, and management
commands write to a locked experiment the way `./mutint import` does. The command says so
when it does.

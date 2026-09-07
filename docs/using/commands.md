# Management commands

Everything here works the same from `./mutint` in mutint-core and from an assembled project's
entry script — both end at the same dispatcher, so a project inherits every command plus any
its plugins ship.

```bash
./mutint start                # migrate, create an admin, open a browser, run the server
./mutint install              # (re)install every component's requirements
./mutint check                # Django's system checks
./mutint test                 # the full suite
./mutint shell
./mutint makemigrations && ./mutint migrate
./mutint db status            # where the database is, whether it is running, who owns it
./mutint db start             # start it unowned, so it outlives the command
./mutint db stop
./mutint db reset --yes       # throw the database away and start again, empty
```

## Data

```bash
./mutint import <path> ... --project P --experiment E --owner U
                             # import breseq folders, .gd files and reference genomes
./mutint import <path> --experiment-id 4    # ...into an experiment that exists
./mutint delete 4 20 19       # soft-delete experiments by id
./mutint purge_deleted --older-than 30
./mutint load_example         # list the datasets components ship; name one to load it
./mutint reannotate <id>      # recompute annotations against the stored reference
./mutint coverage [<id>]      # backfill coverage for samples imported before it existed
./mutint rename_contigs <id>  # rename an experiment's contigs, telling every plugin
./mutint ncbi_accessions      # record or check NCBI accessions for stored references
./mutint relabel_samples <id> # one-shot cleanup of labels from the retired coordinate format; --dry-run first
./mutint project_access       # grant or revoke a role on a project from the shell
```

`load_example` loads a directory of real files through the real import path, so the derived
data it exists to demonstrate is genuinely computed rather than fixtured. Each dataset ships a
README stating the expected answer.

## Background work and housekeeping

```bash
./mutint db_worker            # run queued work (coverage derivation, breseq runs); --batch drains and exits
                              #   `./mutint start` runs one for you; this is for everywhere else
./mutint reap_uploads         # discard staged uploads nobody finalized (cron's job)
./mutint reap_jobs            # discard queue rows no worker ever claimed; --dry-run first
```

## Derived data

Most pages compute what they show when you look at it. What is still stored and rebuilt when
the mutations change is the dashboard's installation-wide totals and whatever the installed
plugins register.

```bash
./mutint rebuild --list                 # what is registered, what is stale, what last failed
./mutint rebuild 4                      # everything stale for experiment 4
./mutint rebuild --all --force          # every live experiment, stale or not
./mutint rebuild 4 --only overview
```

Most of the time nothing here is needed: a page rebuilds its own data when it notices it is
stale. Reach for it after an upgrade, after restoring a database, or when `--list` shows an
error — a failed rebuild is recorded rather than raised, so it is quiet by design.

An entry marked `(manual -- --only runs it)` never rebuilds on its own. That is derived data
expensive or specific enough that recomputing it unasked would be wrong; its own page says
when it has gone stale.

## Versions and documentation

```bash
./mutint version              # every component's version, the assembly's first
./mutint version --bump patch --component mutint-core
./mutint docs                 # build this project's manual
./mutint docs --serve         # and read it at :8001
```

`version` reports each installed component, and an assembled project's own number leads the
list. With more than one component installed `--component` is required -- bumping the wrong
repository's version is quiet, and the mistake only surfaces at release.

## Upgrading

```bash
./mutint upgrade --check      # what is available; change nothing
./mutint upgrade              # move this installation onto it
./mutint upgrade --to v1.4.0  # a particular version
```

`upgrade` moves the working tree and stops there, so run `./mutint start` afterwards: that is
what installs new dependencies and applies new migrations. See [Upgrading](upgrading.md).

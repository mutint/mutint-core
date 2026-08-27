# Management commands

Everything here works the same from `./aledb` in aledb-core and from an assembled project's
entry script — both end at the same dispatcher, so a project inherits every command plus any
its plugins ship.

```bash
./aledb start                # migrate, create an admin, open a browser, run the server
./aledb install              # (re)install every component's requirements
./aledb check                # Django's system checks
./aledb test                 # the full suite
./aledb shell
./aledb makemigrations && ./aledb migrate
```

## Data

```bash
./aledb upload <path> ...    # import breseq output directories
./aledb delete 4 20 19       # soft-delete experiments by id
./aledb purge_deleted --older-than 30
./aledb load_example         # list the datasets components ship; name one to load it
./aledb reannotate <id>      # recompute annotations against the stored reference
./aledb coverage [<id>]      # backfill coverage for samples imported before it existed
```

`load_example` loads a directory of real files through the real import path, so the derived
data it exists to demonstrate is genuinely computed rather than fixtured. Each dataset ships a
README stating the expected answer.

## Derived data

Almost nothing user-facing is computed when you look at it. The needle plot, the Overview's
counts, the dashboard totals and each plugin's tables are all recomputed when the mutations
change.

```bash
./aledb rebuild --list                 # what is registered, what is stale, what last failed
./aledb rebuild 4                      # everything stale for experiment 4
./aledb rebuild --all --force          # every live experiment, stale or not
./aledb rebuild 4 --only overview
```

Most of the time nothing here is needed: a page rebuilds its own data when it notices it is
stale. Reach for it after an upgrade, after restoring a database, or when `--list` shows an
error — a failed rebuild is recorded rather than raised, so it is quiet by design.

An entry marked `(manual -- --only runs it)` never rebuilds on its own. That is derived data
expensive or specific enough that recomputing it unasked would be wrong; its own page says
when it has gone stale.

## Versions and documentation

```bash
./aledb version              # aledb-core's version, not Django's
./aledb version --bump patch
./aledb docs                 # build this project's manual
./aledb docs --serve         # and read it at :8001
```

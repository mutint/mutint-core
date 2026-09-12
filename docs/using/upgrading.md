# Upgrading

MutInt upgrades **in place**. Your data stays where it is — the database in `data/db` and the
file store in `data/store` — and only the code moves.

That is why MutInt is installed as a git checkout and why there is no downloadable archive:
unpacking a new version over an installation that already holds data has no safe answer, since
the data lives inside the directory you would be replacing. Git already knows to leave ignored
files alone, which is the whole reason this works.

## From the sidebar

Click your username, then **Update**. The page is superusers-only.

It lists every component this installation is made of, with its version and the git revision it
is at, and it has two buttons:

- **Check for updates** asks the remote what is available. It is the only thing on the page
  that touches the network; the page itself always renders instantly from the last answer.

    It tells you what you would be getting, not just its name: which commit, when it was
    committed, how many commits ahead of you it is, and **which components it moves** — a
    version is a commit of the whole assembly, and installing it moves each plugin to the
    revision that commit pins. On the Development channel, where `main` moves several times a
    day, the timestamp is usually the thing that tells you whether this is the change you are
    waiting for.

- **Install update** stages the version it found.

Then **quit MutInt and start it again**. The upgrade is applied before the server comes up,
because a running MutInt cannot safely replace the code it is executing. On a Mac that is a
double-click on `MutInt.app`; in a terminal it is Ctrl-C and `./mutint start`.

That one launch does everything: it moves the working tree, installs any dependencies the new
version added, installs any new external tools, and applies any new migrations. You do not have
to run `install` or `migrate` yourself.

## From a terminal

```bash
./mutint upgrade --check          # what is available; change nothing
./mutint upgrade                  # check, then move onto it
./mutint upgrade --to v1.4.0      # a particular version
./mutint upgrade --channel main   # remember which channel to follow
```

`./mutint upgrade` moves the working tree and stops there, so **run `./mutint start`
afterwards** — until you do, the code has moved and the database has not.

## Two channels

| Channel | What it follows |
|---|---|
| **Releases** | tagged versions only — the default, and what a shared installation should run |
| **Development** | the `main` branch, so every change as it lands |

Development is useful for trying a fix before it is released. It carries work that has not been
through a release, so a change that turns out to be wrong reaches you before anyone has had the
chance to notice.

## What it does first

**It backs the database up.** A `pg_dump` is written to `data/backups/` before the working tree
moves, named for the version you are leaving. `--no-backup` skips it. A deployment pointed at
its own PostgreSQL server (`MUTINT_DB_HOST`) is skipped too — backups there are the operator's.

**The database, and only the database.** `data/store` — the reads, the BAMs, the coverage
BigWigs, breseq's reports — is not in the dump. It does not need to be: an upgrade never
touches it, so the rows a restore brings back still point at files that are still there. It is
also typically hundreds of times the size of the dump, which is what makes keeping several of
these affordable.

**The last five are kept.** Each dump is the whole database, and the Development channel can
upgrade daily, so the newest five are kept and older ones are deleted as each new dump lands.
Only files this wrote are eligible — a dump you took yourself and named yourself stays. If you
want one kept indefinitely, rename it.

**It refuses to touch a checkout somebody is working in.** If there are uncommitted changes, or
commits the remote has not seen, the upgrade stops and names what it found rather than
discarding it. That is a development checkout, and `git pull` is the right tool for one.

## After it

An upgraded installation sits on a **detached HEAD**, which is correct: it is pinned to a
release rather than following a branch. `git status` says so, and it is not a problem to fix.

`./mutint rebuild --all --force` recomputes derived data. An upgrade does this for you; it is
worth knowing about if you ever restore a database by hand.

## If something goes wrong

The **Update** page reports what the last attempt did, including a failure — an upgrade that
cannot run is recorded rather than stopping MutInt from starting, so the page is where you find
out.

To go back, upgrade to the version you came from:

```bash
./mutint upgrade --to v1.3.0
```

Going *back* across a migration is not something any tool can do safely in general, which is
what the dump in `data/backups/` is for.

!!! note "Migrations only ever go forward"

    A migration is fixed the moment it is published, and later versions add to it rather than
    rewriting it — see [Releasing](../contributing/releasing.md). That holds on the Development
    channel too, which is what makes the channel safe to run with data: the migrations you take
    part-way between releases are the same ones the release ships, so upgrading on to it never
    asks you to start again.

    If your database ever holds a migration the installed code does not ship, `./mutint start`
    refuses to migrate and says so, instead of failing later with an error about a table that
    already exists. It is a bug worth reporting.

## Installations that do not upgrade themselves

A deployment can turn the feature off with `MUTINT_UPGRADE_ENABLED = False`, and ALEdb does:
it is served from a private repository and upgraded deliberately by whoever runs it. The
**Update** page still lists what is installed; it simply offers no buttons.

# Cutting a release

MutInt is several repositories that version independently and are assembled by submodule
pointer. There is no release automation and deliberately no changelog generator: a release is a
version bump, a tag, and the discipline described here about migrations.

## The one rule

**A migration becomes immutable the moment it is pushed to `main` — and not one second
before.**

Everything else on this page follows from it. While a migration exists only in your checkout it
is a draft: no database anywhere has applied it, so you may delete it, rewrite it, or fold six
of them into one, and nobody can tell. Once it is on `main`, somebody's `django_migrations`
table has a row naming that file, and deleting or renaming it strands their database — `migrate`
has nothing to run and no way to reason about what state they are in.

!!! warning "It used to say *ships in a tag*, and that is no longer true"

    MutInt upgrades itself, and its **Development** channel follows `main` (see
    [Upgrading](../using/upgrading.md)). So `main` is a distribution channel: a migration pushed
    there is applied by every installation on that channel, usually within a day.

    That kills the tidy-the-history-before-tagging step this page used to prescribe. Someone
    part-way between releases would have applied `0002_foo` and `0003_bar`; regenerating those
    as one at release time leaves their database naming two files that no longer exist, and
    `./mutint start` refuses to migrate rather than corrupt their schema
    (`mutint_common/migration_guard.py`). They would be stranded short of the release, with
    nothing to do but reset. **The development channel is only usable if the history it ships
    is the history that gets released.**

So the development history and the published history are now the same history, and a release
adds no migration step of its own. You will make a dozen model decisions between releases and
generate a migration for each; all dozen ship. The cost is a longer history, which is cheap —
the whole suite holds fourteen migration files — and much cheaper than a channel nobody with
data can use.

**Never edit a pushed migration either.** Renaming and deleting are the loud failures; amending
one in place is the quiet one, because the database records only that `0002_foo` was applied and
will never run your amendment. Add a new migration instead.

## Cutting one

Work in the component repository — `mutint-core`, or a plugin.

```bash
# 1. Prove the release migrates from the last one, on a database built there.
git stash && git checkout v1.1.0
./mutint db reset --yes && ./mutint migrate
git checkout - && git stash pop
./mutint migrate                       # must apply exactly the new migrations, no errors
./mutint makemigrations --check        # must say "No changes detected"
./mutint test

# 2. Bump and tag.
./mutint version --bump minor --component mutint-core
git commit -am "chore: release 1.2.0"
git tag v1.2.0
```

**There is no migration step**, and its absence is the point of the rule above: what ships is
what has been on `main` all along.

Step 1 is the one that can fail and the only one that proves anything. Migrating an *old*
database catches a migration that cannot actually be reached from the last released state, and
`makemigrations --check` catches a model change somebody forgot to generate one for. A fresh
database proves neither — and neither does `./mutint check`, which passes with a migration graph
that will not load at all.

`./mutint version --bump` rewrites `mutint_common/version.py` and prints the tag command; it
does not commit, tag, or touch a changelog, because these repositories do not commit on their
own. It requires `--component` whenever more than one component is installed, rather than
guessing which repository you meant.

### The assembled projects

`mutint` and `aledb` are not released this way. They carry no code of their own — they are a
set of submodule pointers — so "releasing" one is bumping its pointers and committing, and their
own version strings (`mutint/config/version.py`; aledb has none and reuses core's) are bumped by
hand when they mean something.

A bump that carries migrations is verified by migrating, not by looking at pages. See the
suite `CLAUDE.md` under **How much to verify after a bump**.

## Squashing, when the history really is too long

Folding migrations that have already been pushed is a different operation from deleting them,
and it is the **only** legitimate way to shorten the history. Django has the mechanism:
`squashmigrations` writes a new migration carrying `replaces = [...]`, listing what it stands in
for.

```bash
./mutint squashmigrations mutint_sample 0001_initial 0009_x --squashed-name v2
```

A database that applied the originals records the squash as already applied and skips it; a
fresh database applies only the squash. Both are correct at the same time, which is the whole
point of `replaces`.

**Deleting the replaced files is a separate release, and a much later one.** While they exist,
both the old names and the new one resolve. Once they are gone, any database that has not yet
reached the squash point can no longer be migrated, and any migration elsewhere that still names
one of them fails at graph-load time. Ship the squash, wait until every installation you care
about is past it — which now includes anyone on the Development channel, not only tagged
deployments — then delete the replaced files and strip `replaces`.

Nothing in the suite has needed this yet.

### `elidable=True` on data migrations

`squashmigrations` cannot drop a `RunPython` or `RunSQL` operation unless it is marked
`elidable=True`, and will tell you so. Decide at the time you write one:

- **`elidable=True`** — the step repairs data that only an existing database can have. A fresh
  database has nothing to repair, so squashing it away is correct.
- **`elidable=False`** (the default) — the step is doing something a fresh database also needs,
  such as seeding rows. It must survive the squash.

Getting this wrong in the elidable direction silently drops a repair from the squashed path.

## What a plugin must never do

**Never depend on another component's migration by filename.** A plugin is a separate
repository; core is free to renumber, squash or collapse its history without knowing your plugin
exists, and a stale name raises `NodeNotFoundError` when the graph loads — which takes down
every management command in the assembled project, not just `migrate`.

Use Django's sentinel instead:

```python
dependencies = [
    ('mutint_experiment', '__first__'),
    ('mutint_sample', '__first__'),
    migrations.swappable_dependency(settings.AUTH_USER_MODEL),
]
```

A foreign key needs the referenced table to exist, and core's models are created in their app's
first migration — later ones only add fields and constraints to core's own tables. `__first__`
says exactly that and survives any renumbering, because a squash's `replaces` makes the squashed
migration the new first.

!!! warning "`makemigrations` will revert this silently"

    Django's autodetector emits concrete migration names and will never emit `__first__`.
    Regenerating a plugin's migration puts the filename back. Re-apply the sentinel whenever you
    regenerate, and keep the comment explaining why it is there.

`mutint-phylogeny` and `mutint-breseq` are the worked examples.

## Beware: `check` cannot see a broken migration graph

`./mutint check` passes with a migration graph that cannot be loaded. So does starting a shell.
Only `migrate` and `test` find it, which is why both appear in the procedure above and why the
guard against this is a test rather than a system check.

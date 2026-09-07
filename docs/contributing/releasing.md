# Cutting a release

MutInt is several repositories that version independently and are assembled by submodule
pointer. There is no release automation and deliberately no changelog generator: a release is a
version bump, a tag, and the discipline described here about migrations.

## The one rule

**A migration becomes immutable the moment it ships in a tag — and not one second before.**

Everything else on this page follows from it. Before a tag, a migration is a draft that exists
only in your checkout: no database anywhere has applied it, so you may delete it, rewrite it, or
fold six of them into one, and nobody can tell. After a tag, somebody's `django_migrations`
table has a row naming that file, and deleting it strands their database — `migrate` has nothing
to run and no way to reason about what state they are in.

This is why the development history and the published history are allowed to differ, and should.
You will make a dozen model decisions between releases and generate a migration for each. None
of them needs to be anybody else's problem.

!!! note "The rule is currently vacuous, and will not stay that way"

    No repository in the suite has a `v<version>` tag yet. Until the first one, *every*
    migration is a draft and the history can be thrown away wholesale — which is exactly what
    happened twice in September 2026. The first tag ends that, permanently.

## Cutting one

Work in the component repository — `mutint-core`, or a plugin.

```bash
# 1. What has this release added? These are drafts; nobody outside has them.
git diff --name-only --diff-filter=A v1.1.0 HEAD -- '*/migrations/'

# 2. Throw them away and say the same thing once.
rm <the files listed above>
./mutint makemigrations --name release_1_2_0

# 3. Prove it against a database built at the previous release, not a fresh one.
git stash && git checkout v1.1.0
./mutint db reset --yes && ./mutint migrate
git checkout - && git stash pop
./mutint migrate                       # must apply exactly the new migrations, no errors
./mutint makemigrations --check        # must say "No changes detected"
./mutint test

# 4. Bump and tag.
./mutint version --bump minor --component mutint-core
git commit -am "chore: release 1.2.0"
git tag v1.2.0
```

Step 3 is the one that can fail and the only one that proves anything. `makemigrations --check`
catches a regenerated migration that quietly lost a field; migrating an *old* database catches
the case where the collapsed migration cannot actually be reached from the last released state.
A fresh database proves neither.

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

## Squashing across releases

Sometimes you want to fold migrations that have *already* shipped. That is a different
operation, and Django has the mechanism: `squashmigrations` writes a new migration carrying
`replaces = [...]`, listing what it stands in for.

```bash
./mutint squashmigrations mutint_sample 0001_initial 0009_x --squashed-name v2
```

A database that applied the originals records the squash as already applied and skips it; a
fresh database applies only the squash. Both are correct at the same time, which is the whole
point of `replaces`.

**Deleting the replaced files is a separate release.** While they exist, both the old names and
the new one resolve. Once they are gone, any database that has not yet reached the squash point
can no longer be migrated, and any migration elsewhere that still names one of them fails at
graph-load time. Ship the squash, wait until every deployment you care about is past it, then
delete the replaced files and strip `replaces` in a later release.

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

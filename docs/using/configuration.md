# Configuration

## Settings

| module | for |
|---|---|
| `config/defaults.py` | the base; delegates to `aledb_common.base_settings` |
| `config/settings_local.py` | local development — SQLite, `DEBUG=True`, no external services |
| `config/settings_private.py` | production, with authentication enforced |
| `config/settings_public.py` | a public read-only deployment |

Select one with `DJANGO_SETTINGS_MODULE`. `./aledb start` writes `settings_local.py` on first
run.

## Storage

`ALEDB_STORE_DIR` is where references and alignments live, keyed by database id. It is the one
setting a real deployment must think about — it holds the BAMs.

!!! danger "Point it away from anything you care about before running tests"

    Store paths are derived from primary keys and a test database numbers its experiments from
    1. A test that exercises the importer and forgets to override the store writes its
    fixtures into `experiments/1/` of whatever deployment it was run against.

    The test runner redirects `ALEDB_STORE_DIR` to a temporary directory as a backstop, which
    exists because this happened: a run replaced a real REL606 reference with a 6 kb synthetic
    one, and the genome browser then drew empty tracks for 29 samples whose BAMs were
    perfectly intact.

## Authentication

Pluggable by changing `INSTALLED_APPS`. Any app with `auth_app = True` on its `AppConfig` and
`app_name = 'accounts'` in its `urls.py` is discovered automatically.

- `aledb_accounts_noauth` — the default. Django's built-in login, nothing enforced.
- `aledb_accounts` — production, with `django-defender` brute-force protection.

## Filtering

An experiment has one filter: a **frequency range** and a list of **ignored genes**. A mutation
is hidden when its frequency falls outside the range, or when *every* gene it touches is on the
list — ignoring one gene of two ignores nothing.

!!! warning "The filter is shared, not personal"

    It is stored per experiment, with no user field, so a cutoff one person changes is a cutoff
    everyone sees. Editing it needs **write** access to the project, and a locked experiment
    refuses it like any other write.

    What *is* per-viewer is the **Show Filtered** checkbox on a mutation table: it reveals what
    the filter hides for you alone, in that request, and stores nothing.

**Every mutation table says what filtering produced it**, in a line under the controls: the
cutoffs in force, the genes excluded, and a link to change them. A table whose filter hides
nothing says *No filtering* rather than describing a range that excludes nothing — those are
different facts about a deployment.

A page that filters by its own rules says so instead. The phylogeny page is the one to know
about: it encodes frequency in three states rather than excluding on it, so a call between 0
and 100 % becomes *ambiguous* rather than absent, and its site count will not match the
mutation tables.

Filtering is not deleting. A filtered mutation is still stored and comes back when the filter
changes; a deleted one is removed from the sample, recorded against whoever did it, and
restorable from the mutation editor's history. The two were once the same mechanism and that
was the bug — the old ignored-mutation lists were a delete that kept the row, per experiment,
attributed to nobody, with no way back.

There used to be a second, installation-wide ignored-gene list. It was removed; whatever it
held was folded into each experiment's own list, where it can be edited by anyone with write
access rather than only by a superuser.

## Access

Four ordered roles, granted on a **project** and nowhere else:

```
read  <  write  <  admin  <  owner
```

`read` sees the project and its data. `write` adds, edits and curates. `admin` additionally
manages access and may delete the project. `owner` additionally grants ownership. Groups can
hold any role except owner — ownership has to be answerable about a person.

Three things confer a role with no grant row: a superuser is owner everywhere, a project's
primary owner is owner of it, and a public project gives everyone `read`. **There is no
blanket grant for staff.**

## Locking an experiment

An admin can lock an experiment, and a lock outranks every role: while it is set the experiment
refuses every web write from everyone, superusers included. It answers "is this dataset still
open", which is a different question from "who are you". Rebuilds are deliberately exempt, so
derived data still keeps up, and management commands still write — the lock guards the web.

## Branding

aledb-core is unbranded: `/` is the project list, the sidebar carries no name, no institution
is credited. A deployment adds its own through `ALEDB_BRANDING` and by supplying templates at
known paths, because an assembled project's `templates/` directory is searched ahead of every
app's.

The `Powered by ALEdb` line at the foot of the sidebar is not branding and has no setting. It
is aledb-core's attribution and renders on every deployment.

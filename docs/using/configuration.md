# Configuration

## Settings

| module | for |
|---|---|
| `config/defaults.py` | the base; delegates to `mutint_common.base_settings` |
| `config/settings_local.py` | local development: `DEBUG=True` on top of the defaults |
| `config/settings_private.py` | production, with authentication enforced |

Select one with `DJANGO_SETTINGS_MODULE`. `settings_local.py` is committed and is what `./mutint` selects.

## Storage

`MUTINT_STORE_DIR` is where references and alignments live, keyed by database id. It is the one
setting a real deployment must think about — it holds the BAMs.

!!! danger "Point it away from anything you care about before running tests"

    Store paths are derived from primary keys and a test database numbers its experiments from
    1. A test that exercises the importer and forgets to override the store writes its
    fixtures into `experiments/1/` of whatever deployment it was run against.

    The test runner redirects `MUTINT_STORE_DIR` to a temporary directory as a backstop, which
    exists because this happened: a run replaced a real REL606 reference with a 6 kb synthetic
    one, and the genome browser then drew empty tracks for 29 samples whose BAMs were
    perfectly intact.

## Authentication

Pluggable by changing `INSTALLED_APPS`. Any app with `auth_app = True` on its `AppConfig` and
`app_name = 'accounts'` in its `urls.py` is discovered automatically.

- `mutint_accounts` — the only one shipped. Django's built-in login, nothing enforced.

The routes and templates live in `mutint_common`, not in the app, so a replacement inherits
login, logout and change-password rather than restating them. **MutInt ships no brute-force
protection**; an app that adds it is the intended way to have it.

## Filtering

Two controls sit above every mutation table: a **frequency range** and a list of **ignored
genes**. A mutation is hidden when its frequency falls outside the range, or when *every* gene
it touches is on the list — ignoring one gene of two ignores nothing.

!!! note "The filter is yours alone"

    It lives in your session, not in a table. Changing it changes nothing for anybody else,
    nothing records that you did it, and it asks for no permission — a locked experiment
    filters like any other, because there is nothing here to protect.

    It used to be the opposite, and the change is worth knowing if you remember the old
    behavior: there was one row per experiment, editable by anyone with write access, so a
    cutoff one person set was a cutoff everyone saw, silently. That conflated *curating a
    dataset* — which is the mutation editor's job, and is attributed and reversible — with
    *choosing what you want to look at*, which is nobody else's business.

    A filter is remembered per experiment, so narrowing one leaves the others alone. The
    controls also read from the query string, and what a URL says wins and is then remembered:
    a link you paste to a colleague shows them the same rows you were looking at, and leaves
    their session filtering that experiment the same way. The **Clear** link works by the same
    rule — it is a URL that says *no filter*.

There is no separate filter page and no **Show Filtered** checkbox. Both existed for the
shared filter: one to edit what everyone saw, the other to see through what somebody else had
hidden from you. Neither is a question you have about your own filter, where the controls are
on the table itself and clearing them is one click.

**Every mutation table says what filtering produced it**, in a line under the controls: the
cutoffs in force and the genes excluded. A table whose filter hides nothing says *No
filtering: every stored mutation for this experiment is shown* rather than describing a range
that excludes nothing — those are different facts about what you are looking at.

A page that filters by its own rules says so instead. The phylogeny page is the one to know
about: it encodes frequency in three states rather than excluding on it, so a call between 0
and 100 % becomes *ambiguous* rather than absent, and its site count will not match the
mutation tables. Search says so too, because it spans experiments and no one filter applies.

!!! warning "The ancestor is not part of your filter"

    If an experiment designates an ancestral sample, its mutations are subtracted from every
    other sample before anything is computed, and the sample itself leaves most listings. That
    is a fact about the dataset rather than a view of it: it is shared, it is permanent, and
    **there is no toggle** — so the summary line names it separately from your own filtering,
    and links to the sample it is about.

Filtering is not deleting. A filtered mutation is still stored and comes back when the filter
changes; a deleted one is removed from the sample, recorded against whoever did it, and
restorable from the mutation editor's history. The two were once the same mechanism and that
was the bug — the old ignored-mutation lists were a delete that kept the row, per experiment,
attributed to nobody, with no way back.

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

mutint-core is unbranded: `/` is the project list, the sidebar carries no name, no institution
is credited. A deployment adds its own through `MUTINT_BRANDING` and by supplying templates at
known paths, because an assembled project's `templates/` directory is searched ahead of every
app's.

The `Powered by ALEdb` line at the foot of the sidebar is not branding and has no setting. It
is mutint-core's attribution and renders on every deployment, and links to
[aledb.org](https://aledb.org/). It carries no version number:
`./mutint version` reports the platform's, and `/about` lists the version and git revision of
every installed component.

The name at the top of the sidebar — whatever `MUTINT_BRANDING['name']` says, or the word
**Dashboard** when there is no branding — links to `/dashboard`, the installation's inventory
of projects, experiments, samples and mutations. There is no separate sidebar entry for it.

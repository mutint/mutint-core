# Assembling a project

This is the other side of the plugin story. A **plugin** adds an app to MutInt; an **assembled
project** is the repository that collects mutint-core, the plugins a deployment wants, and any
apps of its own into one runnable Django project. `mutint` is the reference example.

If you are writing a plugin you do not need to build one of these — use `mutint` and add your
submodule to it. Read this when you are standing up a new deployment.

---

This guide explains how to build an assembled Django project (e.g. `mutint`) that uses
`mutint-core` as a git submodule, plus whichever plugins you want — without modifying
mutint-core. A project needs no app of its own; if you want one, copy `mutint-example`, the
stub plugin, and add it as one more submodule.

## Three repos, three concerns

| Repo | Role |
|------|------|
| **`mutint-core`** | This repo. Provides all core MutInt Django apps. |
| **a plugin** | Optional, one repo each. `mutint-example` is the stub to copy. |
| **`mutint`** | The assembled project repo. Owns `config/`, wires everything together via submodules. |

---

## Directory structure of `mutint`

```
mutint/
├── mutint-core/          # git submodule → mutint-core repo
├── mutint-example/      # git submodule → a plugin (optional)
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── env/                 # everything the entry script provisions; git-ignored
├── mutint               # management entry point (executable Python script)
├── requirements.txt     # this project's own additions only — see below
└── tools.txt            # non-Python tools, if this project needs any of its own
```

**The entry script is named for the project** — `./mutint` for MutInt, `./aledb` for ALEdb.
mutint-core's own is `./mutint` as well, because run standalone it *is* MutInt.
It is the one command anybody runs, so it is the one thing that says which checkout they are
standing in, and three checkouts of the same platform on one machine are the normal case here.

---

## Setting up the repo

```bash
git init mutint && cd mutint
git submodule add <mutint-core-url> mutint-core
git submodule add <plugin-url> mutint-example   # optional, one per plugin
mkdir config && touch config/__init__.py
```

When cloning an existing assembled project:

```bash
git clone --recurse-submodules <mutint-url>
```

---

## config/settings.py

```python
import os, sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTINT_CORE_DIR = os.path.join(BASE_DIR, 'mutint-core')
PLUGIN_DIR = os.path.join(BASE_DIR, 'mutint-example')   # one per plugin

# Append (not insert) so this project's config/ takes precedence over mutint-core's.
sys.path.append(MUTINT_CORE_DIR)
sys.path.append(PLUGIN_DIR)

from mutint_common.base_settings import get_base_settings
globals().update(get_base_settings(BASE_DIR, mutint_core_dir=MUTINT_CORE_DIR))

INSTALLED_APPS += ['mutint_example']   # each plugin's app; it registers its own routes
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
```

`get_base_settings()` returns all core MutInt settings as a dict. Any key can be overridden
by assigning after the `globals().update(...)` call.

---

## config/urls.py

```python
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, re_path
from mutint_common.urls import get_core_urlpatterns

urlpatterns = get_core_urlpatterns()   # core's routes and every plugin's

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
        re_path(r'^__debug__/', include(debug_toolbar.urls)),
    ] + staticfiles_urlpatterns()
```

`get_core_urlpatterns()` returns the full set of mutint-core URL patterns. Append your
own patterns after it.

---

## config/wsgi.py

```python
import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
application = get_wsgi_application()
```

---

## Management entry point

**Copy `mutint/mutint` to your repo root under your own project's name and make it
executable.** Do not write one — mutint-core's `./mutint`, `mutint/mutint` and
`aledb/aledb` are byte-identical by design. Nothing in it names a project:
everything is derived from `BASE_DIR`, the script's own location. The components come from
`.gitmodules` beside it, and the database name from the checkout's directory
(`mutint_common/pg.py`), which is why three checkouts on one machine get three databases
without being configured for it.

It ends in the four lines you would have written by hand:

```python
from mutint_common.cli import manage
if __name__ == '__main__':
    manage()
```

Everything above them is why it is not four lines. Before Django is importable at all it
provisions a pinned Python into `env/python`, builds `env/main` from it, installs every
component's `requirements.txt` and `tools.txt`, provisions and starts a PostgreSQL cluster
under `env/`, exports `MUTINT_TOOLS_DIR` and the database connection, and re-execs itself
under the venv. It discovers the components to do that for by reading `.gitmodules`, so a
copied script needs no edit when you add a submodule.

**Anything run outside it will not find the database.** `env/main/bin/python manage.py …`
connects to libpq's default socket and fails, because the connection is exported by the
script and cannot be derived in settings. All Django management commands work through it:
`./mutint migrate`, `./mutint shell`, and so on.

---

## requirements.txt and tools.txt

**Do not chain `-r mutint-core/requirements.txt`.** The entry script walks `.gitmodules` and
installs every component's `requirements.txt` itself, so a chain installs mutint-core's twice
and, worse, describes a mechanism that is not the one running. The project's own file is for
dependencies the *project* adds, and mutint's is empty but for a comment saying so.

`tools.txt` beside it is the same idea for non-Python tools, as conda package specs, collected
the same way and installed into `env/tools` with micromamba. It cannot be a Django registry:
installation happens before Django exists. Code finds them through `mutint_common.tools`.

---

## First-time setup and running

```bash
./mutint start   # first run: provisions env/, migrates, creates an admin, spawns a worker,
                 # opens a browser, starts the server
./mutint install # (re)install dependencies only
```

That is the whole of it on a clean machine — **nothing is installed on the host** and no
PostgreSQL server is something you supply. There is deliberately no documented manual path:
hand-building a `.venv` and running `manage.py` gets you an interpreter with no database
behind it, which fails in a way that reads like a broken checkout.

Or step by step, still through the entry script:

```bash
./mutint migrate --run-syncdb
./mutint createsuperuser
./mutint runserver
```

---

## What you get from mutint-core without changes

- The data models, the import pipeline, the mutation views and tables, export, filtering,
  stats, the genome browser and the mutation editor — every core `mutint_*` app.
  **Fixation and convergence are not among them**: both are plugin repos, as compare, the
  needle plot and phylogeny are, and an assembled project gets them by listing them in
  `.gitmodules`. A component you do not install contributes nothing rather than an empty page.
- `./mutint start` first-run setup, and the provisioned `env/` behind it
- Auth slot — `mutint_accounts_noauth` is the only implementation shipped; swap in your own
  by changing `INSTALLED_APPS` in your `config/settings.py` and setting `auth_app = True` on
  its `AppConfig`
- The eight registries in `mutint_common/`, which are how an app contributes to a core page
  without core importing it: nav entries, About sections, import types, Overview panels,
  export handlers, experiment-view context, example datasets and derived-data rebuilds. See
  [The registries](../plugin/registries.md), and the Reference pages generated from their
  docstrings.

**This section used to end with a list of integration points mutint-core "will gain"** —
custom panels on experiment detail views, hooks into the upload pipeline, and sidebar
navigation. All three shipped: `panel_registry`, `plugin_registry` with `rebuild_registry`,
and `nav_registry` respectively. The list is gone rather than corrected, because what
replaced it is the bullet above.

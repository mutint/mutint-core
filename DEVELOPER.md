# Developer Guide: Assembling a Project with aledb-core

This guide explains how to build an assembled Django project (e.g. `mutint`) that uses
`aledb-core` as a git submodule and a custom app (`mutint-app`) from a third repo — without
modifying aledb-core.

## Three repos, three concerns

| Repo | Role |
|------|------|
| **`aledb-core`** | This repo. Provides all core ALEdb Django apps. |
| **`mutint-app`** | Your custom app repo. Contains a Django app named `mutint_app`. |
| **`mutint`** | The assembled project repo. Owns `config/`, wires everything together via submodules. |

---

## Directory structure of `mutint`

```
mutint/
├── aledb-core/          # git submodule → aledb-core repo
├── mutint-app/          # git submodule → mutint-app repo
├── config/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── aledb                # management entry point (executable Python script)
└── requirements.txt
```

---

## Setting up the repo

```bash
git init mutint && cd mutint
git submodule add <aledb-core-url> aledb-core
git submodule add <mutint-app-url> mutint-app
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
ALEDB_CORE_DIR = os.path.join(BASE_DIR, 'aledb-core')
MUTINT_APP_DIR = os.path.join(BASE_DIR, 'mutint-app')

# Append (not insert) so this project's config/ takes precedence over aledb-core's.
sys.path.append(ALEDB_CORE_DIR)
sys.path.append(MUTINT_APP_DIR)

from aledb_common.base_settings import get_base_settings
globals().update(get_base_settings(BASE_DIR, aledb_core_dir=ALEDB_CORE_DIR))

INSTALLED_APPS += ['mutint_app']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
```

`get_base_settings()` returns all core ALEdb settings as a dict. Any key can be overridden
by assigning after the `globals().update(...)` call.

---

## config/urls.py

```python
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, re_path
from aledb_common.urls import get_core_urlpatterns

urlpatterns = get_core_urlpatterns() + [
    re_path(r'^mutint/', include('mutint_app.urls')),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
        re_path(r'^__debug__/', include(debug_toolbar.urls)),
    ] + staticfiles_urlpatterns()
```

`get_core_urlpatterns()` returns the full set of aledb-core URL patterns. Append your
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

## Management entry point (`aledb`, executable)

Create this file at the repo root and make it executable (`chmod +x aledb`):

```python
#!/usr/bin/env python
import os, sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, 'aledb-core'))
sys.path.append(os.path.join(BASE_DIR, 'mutint-app'))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from aledb_common.cli import manage
if __name__ == '__main__':
    manage()
```

This mirrors aledb-core's own `./aledb` script but points at your assembled project's
settings. All Django management commands work: `./aledb migrate`, `./aledb shell`, etc.

---

## requirements.txt

```
-r aledb-core/requirements.txt
# add mutint-specific dependencies here
```

---

## First-time setup and running

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

./aledb start    # runs migrate, creates admin superuser, opens browser, starts server
```

Or step by step:

```bash
./aledb migrate --run-syncdb
./aledb createsuperuser
./aledb runserver
```

---

## What you get from aledb-core without changes

- All ALEdb data models, mutation views, experiment upload, fixation, convergence, and stats
- `./aledb start` first-run setup (migrate + superuser + browser open)
- Auth slot — swap `aledb_accounts_noauth` for `aledb_accounts` or your own implementation
  by changing `INSTALLED_APPS` in your `config/settings.py`
- Context registry — inject data into experiment views from your app without touching
  aledb-core code (see `aledb_common.context_registry`)

---

## Future integration points

aledb-core will gain additional injection stubs for:

- Extending experiment detail views with custom panels
- Hooking into the experiment upload pipeline
- Adding sidebar content and navigation items

These will allow deeper integration without forking aledb-core.

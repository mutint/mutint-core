# Refactor aledb-core for use as a git submodule

## Background

aledb-core is being used as a git submodule in assembled Django projects (e.g., `mutint`).
In those projects, aledb-core lives at a subdirectory like `mutint/aledb-core/`, and its
app packages are made importable by adding that directory to `sys.path`.

The assembled project provides its own `config/` Django package (with `settings.py`,
`urls.py`, etc.) at its root. Because Python can only load one `config` package per
process, the assembled project's `config/` takes precedence and aledb-core's `config/`
is never imported directly.

This creates three sync problems that this refactor eliminates:

1. **`config.context_processors`** — referenced by name in TEMPLATES; assembled projects
   must provide a shim `config/context_processors.py`.
2. **`config.views.protected_file_serve`** — referenced by name in `config/urls.py`;
   assembled projects must provide a shim `config/views.py`.
3. **URL patterns** — `config/urls.py` hardcodes all core URL patterns; assembled projects
   must copy and maintain a duplicate.
4. **Base settings** — `config/defaults.py` is loaded via `importlib` gymnastics in
   assembled projects because importing it normally would collide with their `config`
   namespace.

After this refactor, assembled projects can do all of the following cleanly, with no shims:

```python
# assembled project's config/settings.py
from aledb_common.base_settings import get_base_settings
globals().update(get_base_settings(BASE_DIR, aledb_core_dir=os.path.join(BASE_DIR, 'aledb-core')))
INSTALLED_APPS += ['mutint_app']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

# assembled project's config/urls.py
from aledb_common.urls import get_core_urlpatterns
from django.urls import path
from django.urls import include
urlpatterns = get_core_urlpatterns() + [
    path('mutint/', include('mutint_app.urls')),
]
```

---

## Changes required

### 1. NEW FILE — `aledb_common/context_processors.py`

Move the `global_settings` function out of `config/context_processors.py` and into
`aledb_common`. The content is identical; only the module location changes.

```python
from django.conf import settings


def global_settings(request):
    return {
        'GOOGLE_ANALYTICS_TAG': settings.GOOGLE_ANALYTICS_TAG,
    }
```

`config/context_processors.py` should then be reduced to a backward-compat re-export
so nothing inside aledb-core breaks:

```python
# config/context_processors.py  (keep this file; change its content to:)
from aledb_common.context_processors import global_settings  # noqa: F401
```

---

### 2. NEW FILE — `aledb_common/views.py`

Move **all** view functions and helpers out of `config/views.py` into `aledb_common/views.py`.
The functions to move are: `protected_file_serve`, `_get_file_response`, `_is_valid_pagename`,
`show_amplifiction_data`, `_get_exp_data_folder_name`.

The content of the new file is the current content of `config/views.py` — no changes to
the functions themselves, just the new location.

Then replace `config/views.py` with a backward-compat re-export:

```python
# config/views.py  (keep this file; change its content to:)
from aledb_common.views import (  # noqa: F401
    protected_file_serve,
    show_amplifiction_data,
)
```

---

### 3. NEW FILE — `aledb_common/urls.py`

Create a function that returns the complete set of core URL patterns. This is extracted
from `config/urls.py` and is the canonical source going forward.

```python
"""
Core URL pattern factory for aledb-core-based projects.

Assembled projects import this instead of duplicating the pattern list:

    from aledb_common.urls import get_core_urlpatterns
    urlpatterns = get_core_urlpatterns() + [...]
"""
from django.urls import include, re_path
from django.contrib import admin


def get_core_urlpatterns():
    """Return the full list of URL patterns provided by aledb-core apps."""
    from django.apps import apps as django_apps
    from aledb_common.views import protected_file_serve

    _auth_cfg = next(
        (cfg for cfg in django_apps.get_app_configs() if getattr(cfg, 'auth_app', False)),
        None,
    )

    urlpatterns = [
        re_path(r'^', include('aledb_home.urls')),
        re_path(r'^dashboard', include('aledb_dashboard.urls')),
        re_path(r'^admin/', admin.site.urls),
    ]

    if _auth_cfg:
        urlpatterns += [
            re_path(r'^accounts/', include(f'{_auth_cfg.name}.urls', namespace='accounts'))
        ]

    urlpatterns += [
        re_path(r'^about', include('aledb_about.urls')),
        re_path(r'^ale/', include('aledb_experiment.urls')),
        re_path(r'^bibliome/', include('aledb_bibliome.urls')),
        re_path(r'^converge/', include('aledb_converge.urls')),
        re_path(r'^export', include('aledb_export.urls')),
        re_path(r'^filter/', include('aledb_filter.urls')),
        re_path(r'^fixation/', include('aledb_fixation.urls')),
        re_path(r'^interop-query/', include('aledb_interop_query.urls')),
        re_path(r'^metadata/', include('aledb_metadata.urls')),
        re_path(r'^mutations/', include('aledb_seq.urls')),
        re_path(r'^search/', include('aledb_search.urls')),
        re_path(r'^stats/', include('aledb_stats.urls')),
        re_path(r'^aledata/(?P<page_name>.*)$', protected_file_serve),
    ]

    return urlpatterns
```

---

### 4. MODIFY — `config/urls.py`

Replace the hardcoded URL pattern list with a call to `get_core_urlpatterns()`.
The debug toolbar block stays in `config/urls.py` since it belongs to the
standalone aledb-core project, not the shared core.

```python
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, re_path
from django.conf import settings
from aledb_common.urls import get_core_urlpatterns

urlpatterns = get_core_urlpatterns()

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
        re_path(r'^__debug__/', include(debug_toolbar.urls)),
    ] + staticfiles_urlpatterns()
```

---

### 5. NEW FILE — `aledb_common/base_settings.py`

Extract the settings from `config/defaults.py` into a callable so assembled projects can
inherit them without importing aledb-core's `config` package.

The `base_dir` parameter is the **assembled project's** root (used for `STATIC_ROOT` and
the default SQLite `DATABASES` path). The optional `aledb_core_dir` parameter points to
the aledb-core checkout (used for `STATICFILES_DIRS`); when omitted it defaults to
`base_dir`, which is correct for standalone aledb-core use.

```python
"""
Base settings factory for aledb-core-based projects.

Usage in an assembled project's config/settings.py:

    import os, sys
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ALEDB_CORE_DIR = os.path.join(BASE_DIR, 'aledb-core')

    # Make aledb-core app packages importable before importing from aledb_common.
    sys.path.append(ALEDB_CORE_DIR)

    from aledb_common.base_settings import get_base_settings
    globals().update(get_base_settings(BASE_DIR, aledb_core_dir=ALEDB_CORE_DIR))

    # Extend as needed:
    INSTALLED_APPS += ['mutint_app']
    ROOT_URLCONF = 'config.urls'
    WSGI_APPLICATION = 'config.wsgi.application'
"""
import os
import sys

from django.contrib import messages


def get_base_settings(base_dir, aledb_core_dir=None):
    """
    Return a dict of base Django settings for an aledb-core-based project.

    Args:
        base_dir:       Root directory of the assembled project (or aledb-core itself
                        when running standalone). Used for STATIC_ROOT and the default
                        SQLite database path.
        aledb_core_dir: Path to the aledb-core checkout. Defaults to base_dir (correct
                        for standalone aledb-core use). Used for STATICFILES_DIRS so
                        aledb_common's static assets are always found.
    """
    if aledb_core_dir is None:
        aledb_core_dir = base_dir

    debug = os.environ.get('DEBUG', '0') == '1'

    settings = {
        'DEBUG': debug,

        'GOOGLE_ANALYTICS_TAG': os.environ.get('GOOGLE_ANALYTICS_TAG', ''),
        'SEQUENCING_URL': os.environ.get('SEQUENCING_URL', ''),
        'ALE_DATA_ROOT_DIR': os.environ.get('ALE_DATA_ROOT_DIR', 'ale_data_root_dir'),

        'ALLOWED_HOSTS': [os.environ.get('DJANGO_SERVER_HOST', 'localhost'), 'localhost', '127.0.0.1'],
        'SESSION_EXPIRE_AT_BROWSER_CLOSE': True,
        'SESSION_COOKIE_AGE': 604800,
        'SESSION_SAVE_EVERY_REQUEST': True,

        'CSRF_TRUSTED_ORIGINS': ['http://127.0.0.1:8000', 'http://localhost:8000'],

        'INSTALLED_APPS': [
            'django.contrib.admin',
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'django.contrib.sessions',
            'django.contrib.sites',
            'django.contrib.messages',
            'django.contrib.staticfiles',
            'django_tables2',
            'django_filters',
            'bootstrap3',
            'bootstrap4',
            'debug_toolbar',
            'guardian',
            'aledb_experiment',
            'aledb_import',
            'aledb_seq',
            'aledb_filter',
            'aledb_fixation',
            'aledb_stats',
            'aledb_metadata',
            'aledb_about',
            'aledb_converge',
            'aledb_accounts_noauth',
            'aledb_export',
            'aledb_common',
            'aledb_dashboard',
            'aledb_search',
            'aledb_bibliome',
            'aledb_home',
            'aledb_interop_query',
        ],

        'DATABASES': {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': os.path.join(base_dir, 'aledb_local.sqlite3'),
            },
        },

        'CACHES': {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            },
        },

        'MIDDLEWARE': [
            'django.middleware.common.CommonMiddleware',
            'django.contrib.sessions.middleware.SessionMiddleware',
            'debug_toolbar.middleware.DebugToolbarMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware',
        ],

        'AUTHENTICATION_BACKENDS': (
            'django.contrib.auth.backends.ModelBackend',
            'guardian.backends.ObjectPermissionBackend',
        ),

        'LOGIN_URL': '/accounts/login/',
        'LOGIN_REDIRECT_URL': 'experiments_view',
        'LOGOUT_REDIRECT_URL': 'experiments_view',

        'USE_X_FORWARDED_PORT': os.environ.get('USE_X_FORWARDED_PORT', '0') == '1',

        'TIME_ZONE': 'America/Los_Angeles',
        'LANGUAGE_CODE': 'en-us',
        'SITE_ID': 1,
        'USE_I18N': True,
        'USE_L10N': True,
        'USE_TZ': True,

        'MEDIA_ROOT': '',
        'MEDIA_URL': '',

        'STATIC_ROOT': os.path.join(base_dir, 'static'),
        'STATIC_URL': '/static/',
        'STATICFILES_DIRS': [
            # aledb_common's static assets live inside the aledb-core checkout.
            os.path.join(aledb_core_dir, 'aledb_common', 'staticfiles'),
        ],
        'STATICFILES_FINDERS': (
            'django.contrib.staticfiles.finders.FileSystemFinder',
            'django.contrib.staticfiles.finders.AppDirectoriesFinder',
        ),

        'GUARDIAN_RAISE_403': True,
        'DEFAULT_AUTO_FIELD': 'django.db.models.AutoField',

        'SECRET_KEY': os.environ.get(
            'DJANGO_SECRET_KEY',
            'local-dev-only-insecure-key-change-in-production',
        ),

        'TEMPLATES': [
            {
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'DIRS': [],
                'APP_DIRS': True,
                'OPTIONS': {
                    'context_processors': [
                        'django.contrib.auth.context_processors.auth',
                        'django.template.context_processors.debug',
                        'django.template.context_processors.request',
                        'django.template.context_processors.i18n',
                        'django.template.context_processors.media',
                        'django.template.context_processors.static',
                        'django.template.context_processors.tz',
                        'django.contrib.messages.context_processors.messages',
                        # Moved from config.context_processors to aledb_common so
                        # assembled projects don't need a config/ shim.
                        'aledb_common.context_processors.global_settings',
                    ],
                    'debug': debug,
                },
            },
        ],

        'DEFAULT_EXCEPTION_REPORTER_FILTER': 'django.views.debug.SafeExceptionReporterFilter',

        'LOGGING': {
            'version': 1,
            'disable_existing_loggers': False,
            'filters': {
                'require_debug_false': {'()': 'django.utils.log.RequireDebugFalse'},
                'require_debug_true': {'()': 'django.utils.log.RequireDebugTrue'},
            },
            'formatters': {
                'simple': {
                    'format': '{levelname} {asctime} {name}:{lineno} - {message}',
                    'style': '{',
                },
            },
            'handlers': {
                'console': {
                    'formatter': 'simple',
                    'class': 'logging.StreamHandler',
                    'level': 'DEBUG',
                    'filters': ['require_debug_true'],
                },
            },
            'loggers': {
                '': {'handlers': ['console'], 'level': 'DEBUG', 'propagate': True},
                'django': {'level': 'ERROR', 'handlers': ['console'], 'propagate': False},
            },
        },

        'PUBLIC': os.environ.get('PUBLIC', '0') == '1',
        'PUBLIC_USERNAME': os.environ.get('PUBLIC_USERNAME', 'public'),
        'PUBLIC_PASSWORD': os.environ.get('PUBLIC_PASSWORD', 'REDACTED-CREDENTIAL'),

        'EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend',

        'AUTH_PASSWORD_VALIDATORS': [
            {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
            {
                'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
                'OPTIONS': {'min_length': 9},
            },
            {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
            {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
        ],

        'MESSAGE_TAGS': {messages.ERROR: 'danger'},
    }

    # Switch to SQLite for tests (mirrors the logic in config/defaults.py)
    if ('test' in sys.argv or 'test_coverage' in sys.argv or
            os.environ.get('FORCE_SQLITE') == '1'):
        settings['DATABASES']['default'] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(base_dir, 'dev.sqlite3'),
        }

    return settings
```

---

### 6. MODIFY — `config/defaults.py`

Replace the current content with a call to `get_base_settings` so that
`config/defaults.py` is no longer the canonical definition — it just delegates.
This keeps backward compatibility for any code that imports from `config.defaults`
while making `aledb_common.base_settings` the single source of truth.

```python
"""
Standalone aledb-core base settings.
Delegates to aledb_common.base_settings.get_base_settings() so that assembled
projects can import the same settings without touching the `config` namespace.
"""
import os
import sys
from pathlib import Path  # noqa: F401 — kept for any code that imports it from here

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from aledb_common.base_settings import get_base_settings as _get_base_settings

# Spread all settings into this module's namespace so `from config.defaults import *`
# continues to work exactly as before.
globals().update(_get_base_settings(BASE_DIR))

# ROOT_URLCONF and WSGI_APPLICATION are project-specific; they stay here rather than
# in get_base_settings() because assembled projects must set their own values.
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
```

---

## Verification

After making the changes above, verify that standalone aledb-core still works:

```bash
cd aledb-core
python manage.py check          # should report no issues
python manage.py test           # existing test suite must pass
python manage.py runserver      # spot-check that pages load
```

Spot-check that the refactored pieces are wired correctly:

```python
# Quick smoke test — run with: python -c "..." from the aledb-core directory
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_local'
import django; django.setup()

# 1. context processor is found via aledb_common
from aledb_common.context_processors import global_settings
assert callable(global_settings)

# 2. protected_file_serve is found via aledb_common
from aledb_common.views import protected_file_serve
assert callable(protected_file_serve)

# 3. core URL patterns are accessible
from aledb_common.urls import get_core_urlpatterns
patterns = get_core_urlpatterns()
assert len(patterns) > 5

# 4. base settings function returns expected keys
from aledb_common.base_settings import get_base_settings
s = get_base_settings('/tmp/test')
assert 'INSTALLED_APPS' in s
assert 'aledb_experiment' in s['INSTALLED_APPS']
assert s['STATICFILES_DIRS'] == ['/tmp/test/aledb_common/staticfiles']

s2 = get_base_settings('/tmp/project', aledb_core_dir='/tmp/project/aledb-core')
assert s2['STATICFILES_DIRS'] == ['/tmp/project/aledb-core/aledb_common/staticfiles']

print("All checks passed.")
```

---

## What assembled projects look like after this refactor

The shim files (`config/context_processors.py`, `config/views.py`) are no longer
needed. An assembled project's config is now:

**`config/settings.py`**
```python
import os, sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALEDB_CORE_DIR = os.path.join(BASE_DIR, 'aledb-core')

# Make submodule app packages importable. Append so `import config` still
# resolves to this project's config/, not aledb-core's.
sys.path.append(ALEDB_CORE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'mutint-app'))

from aledb_common.base_settings import get_base_settings
globals().update(get_base_settings(BASE_DIR, aledb_core_dir=ALEDB_CORE_DIR))

INSTALLED_APPS += ['mutint_app']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
```

**`config/urls.py`**
```python
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, re_path, path
from aledb_common.urls import get_core_urlpatterns

urlpatterns = get_core_urlpatterns() + [
    path('mutint/', include('mutint_app.urls')),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
        re_path(r'^__debug__/', include(debug_toolbar.urls)),
    ] + staticfiles_urlpatterns()
```

No `config/context_processors.py` or `config/views.py` needed.

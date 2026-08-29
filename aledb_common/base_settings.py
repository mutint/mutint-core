"""
Base settings factory for aledb-core-based projects.

Standalone use (aledb-core running on its own) — config/defaults.py delegates here.

Submodule use — assembled project's config/settings.py:

    import os, sys
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ALEDB_CORE_DIR = os.path.join(BASE_DIR, 'aledb-core')
    sys.path.append(ALEDB_CORE_DIR)

    from aledb_common.base_settings import get_base_settings
    globals().update(get_base_settings(BASE_DIR, aledb_core_dir=ALEDB_CORE_DIR))

    INSTALLED_APPS += ['myapp']
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
        base_dir:       Root of the assembled project (or aledb-core itself when standalone).
                        Used for STATIC_ROOT and the default SQLite database path.
        aledb_core_dir: Path to the aledb-core checkout. Defaults to base_dir (correct for
                        standalone use). Used for STATICFILES_DIRS so aledb_common's static
                        assets are always found when running as a submodule.
    """
    if aledb_core_dir is None:
        aledb_core_dir = base_dir

    # A deployment drops its own templates and static assets here to override
    # aledb-core's -- see the ALEDB_BRANDING and landing-page notes below. An
    # assembled project reaches this function through aledb-core's config/defaults.py,
    # which passes the aledb-core directory as base_dir, so it must re-point both of
    # these at its own root after calling us.
    project_templates = os.path.join(base_dir, 'templates')
    project_staticfiles = os.path.join(base_dir, 'staticfiles')

    debug = os.environ.get('DEBUG', '0') == '1'

    settings = {
        'DEBUG': debug,

        'GOOGLE_ANALYTICS_TAG': os.environ.get('GOOGLE_ANALYTICS_TAG', ''),

        # The deployment's own identity: {'name': ..., 'version': ..., 'logo': ...},
        # where logo is a path under a staticfiles dir. Empty by default, and an empty
        # value renders nothing -- aledb-core carries no deployment branding of its own.
        # Separate from aledb_common.version, which is the platform's version and is
        # always shown in the "Powered by ALEdb" watermark.
        'ALEDB_BRANDING': {},

        # Managed store that aledb-core owns and operates: uploaded .gd, BAM/BAI, and the
        # per-experiment reference. Every path under here is derived from a database
        # primary key,
        # so no client-supplied path ever reaches the filesystem.
        'ALEDB_STORE_DIR': os.environ.get(
            'ALEDB_STORE_DIR', os.path.join(base_dir, 'aledb_store')),
        # External tools a component declared in its tools.txt, installed there by the
        # entry script. Read from the environment because only the entry script knows the
        # project root: base_dir here is aledb-core's own directory under an assembled
        # project, the same reason templates/ and staticfiles/ have to be re-pointed.
        # None when nothing exported it, and aledb_common.tools then falls back to PATH.
        'ALEDB_TOOLS_DIR': os.environ.get('ALEDB_TOOLS_DIR'),
        # Chunked uploads staged but never finalized are reaped after this many hours.
        'ALEDB_UPLOAD_SESSION_TTL_HOURS': int(
            os.environ.get('ALEDB_UPLOAD_SESSION_TTL_HOURS', '24')),

        'ALLOWED_HOSTS': [os.environ.get('DJANGO_SERVER_HOST', 'localhost'), 'localhost', '127.0.0.1'],
        'SESSION_EXPIRE_AT_BROWSER_CLOSE': True,
        'SESSION_COOKIE_AGE': 604800,
        'SESSION_SAVE_EVERY_REQUEST': True,

        'CSRF_TRUSTED_ORIGINS': ['http://127.0.0.1:8000', 'http://localhost:8000'],

        # A bare `test` in an assembled project discovers nothing: its code lives in
        # submodule directories whose names contain hyphens, so they are not importable
        # packages. The runner substitutes the installed first-party apps instead.
        'TEST_RUNNER': 'aledb_common.test_runner.AledbTestRunner',

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
            # Order is load-bearing: sidebar entries render in INSTALLED_APPS
            # order (see aledb_common/nav_registry.py). To move a nav entry,
            # move its app here. Apps contributing no nav follow.
            'aledb_about',           # nav: About
            'aledb_dashboard',       # nav: Dashboard
            'aledb_search',          # nav: Search
            'aledb_experiment',      # nav: Projects, Experiments
            'aledb_metadata',        # nav: Metadata
            'aledb_seq',             # nav: Mutations
            'aledb_mutation_editor', # nav: Edit Mutations
            'aledb_filter',          # nav: Filter
            'aledb_import',          # nav: none (Add data is reached from an experiment)
            'aledb_stats',
            'aledb_accounts_noauth',
            'aledb_export',
            'aledb_common',
            'aledb_bibliome',
            'aledb_home',
            'aledb_interop_query',
        ],

        'DATABASES': {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': os.path.join(base_dir, 'aledb_local.sqlite3'),
                # Handed to sqlite3.connect. The Add page polls while an import writes, so
                # two connections genuinely contend now; 5s (the default) is not long when
                # the other one is copying an alignment. WAL and the rest are set per
                # connection in aledb_common.sqlite_tuning, which explains the whole of it.
                'OPTIONS': {'timeout': 30},
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

        # Just the model backend. django-guardian's ObjectPermissionBackend sat here to
        # answer `user.has_perm('view_project', project)`; access is an explicit
        # `ProjectAccess` row now and `aledb_experiment/permissions.py` reads it directly,
        # so there is no per-object permission for an auth backend to resolve.
        'AUTHENTICATION_BACKENDS': (
            'django.contrib.auth.backends.ModelBackend',
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
        # The project's own staticfiles/ comes first so a deployment can supply its
        # own logo. Deliberately not named static/ -- that is STATIC_ROOT, and Django
        # refuses a STATICFILES_DIRS entry equal to it. Listed only when it exists,
        # or a project without one trips staticfiles.W004 on every check.
        'STATICFILES_DIRS': (
            ([project_staticfiles] if os.path.isdir(project_staticfiles) else [])
            + [os.path.join(aledb_core_dir, 'aledb_common', 'staticfiles')]
        ),
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
                'DIRS': [project_templates],
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
                        'aledb_common.context_processors.global_settings',
                        'aledb_common.context_processors.plugin_exports',
                        'aledb_common.context_processors.nav_items',
                        'aledb_common.context_processors.branding',
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

    # Switch to SQLite for tests
    if ('test' in sys.argv or 'test_coverage' in sys.argv or
            os.environ.get('FORCE_SQLITE') == '1'):
        settings['DATABASES']['default'] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(base_dir, 'dev.sqlite3'),
            'OPTIONS': {'timeout': 30},
        }

    return settings

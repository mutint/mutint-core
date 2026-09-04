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
from django.core.exceptions import ImproperlyConfigured


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
        # NCBI E-utilities, used by aledb_sample.ncbi to confirm that a reference contig
        # really is the accession somebody said it was. All optional: with none of them set
        # the check still works, just politely slower and anonymously.
        #
        # NCBI asks callers to identify themselves and rate-limits to 3 requests/second, or
        # 10 with a key. Nothing here is a secret in the credential sense -- the key only
        # raises a rate limit -- but it comes from the environment like everything else.
        'ALEDB_NCBI_EMAIL': os.environ.get('ALEDB_NCBI_EMAIL', ''),
        'ALEDB_NCBI_API_KEY': os.environ.get('ALEDB_NCBI_API_KEY', ''),
        # Seconds. Deliberately short: this runs from a management command and from a button,
        # and a check that hangs is worse than one that says it could not reach NCBI.
        'ALEDB_NCBI_TIMEOUT': float(os.environ.get('ALEDB_NCBI_TIMEOUT', '30')),
        # Refuse to stream a record larger than this rather than pulling a human chromosome
        # through a verification that was designed around bacterial genomes.
        'ALEDB_NCBI_MAX_BASES': int(os.environ.get('ALEDB_NCBI_MAX_BASES', str(50_000_000))),

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
            # For `intcomma` alone: the Overview prints uncalled bases, which run to
            # millions on a real genome and are unreadable without separators.
            'django.contrib.humanize',
            # django_tables2, django_filters, bootstrap3 and bootstrap4 were all here and are
            # gone. Not one of them was used: no template loaded a bootstrap tag library, the
            # single `{% load render_table %}` never called the tag, and django_filters' two
            # FilterSet classes were imported by nothing. Four installed apps that had to be
            # re-verified against every framework upgrade in exchange for nothing.
            'debug_toolbar',
            # The queue behind django.tasks. Django ships the API and no runner --
            # its own backends run tasks inline or not at all, and DEP 0014 keeps a
            # worker process out of core deliberately. See aledb_import/tasks.py.
            'django_tasks_db',
            # Order is load-bearing: sidebar entries render in INSTALLED_APPS
            # order (see aledb_common/nav_registry.py). To move a nav entry,
            # move its app here. Apps contributing no nav follow.
            'aledb_about',           # nav: About
            'aledb_dashboard',       # nav: Dashboard
            'aledb_search',          # nav: Search
            'aledb_experiment',      # nav: Projects, Experiments
            'aledb_metadata',        # nav: Metadata
            'aledb_sample',             # nav: Mutations
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

        # PostgreSQL, and only PostgreSQL. The SQLite backend, its BEGIN IMMEDIATE
        # setting and its WAL pragmas are all gone: two supported backends means two
        # configurations, and with no CI only one of them was ever being run.
        #
        # Every value comes from the environment, exported by the entry script before it
        # re-execs -- settings cannot work any of it out, because an assembled project
        # reaches get_base_settings() through aledb-core's config/defaults.py, which passes
        # the *aledb-core* directory as base_dir. Same trap templates/ and staticfiles/ work
        # around, and the reason ALEDB_TOOLS_DIR is exported too.
        #
        # An unset ALEDB_DB_HOST means the entry script manages a local cluster under env/;
        # setting it yourself means it manages nothing and connects where you say. That one
        # variable is the whole of the deployment escape hatch. See aledb_common/pg.py.
        'DATABASES': {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.environ.get('ALEDB_DB_NAME', 'aledb'),
                'USER': os.environ.get('ALEDB_DB_USER', 'aledb'),
                'PASSWORD': os.environ.get('ALEDB_DB_PASSWORD', ''),
                # libpq reads a HOST beginning with "/" as a directory holding a unix socket,
                # which is the only thing the managed cluster listens on.
                'HOST': os.environ.get('ALEDB_DB_HOST', ''),
                'PORT': os.environ.get('ALEDB_DB_PORT', ''),
            },
        },

        # Where enqueued work goes. The call sites use django.tasks' own @task/.enqueue(),
        # so swapping this for Redis, RQ or Celery later is a settings change and touches no
        # code. Overridden to Django's ImmediateBackend for the test suite, in test_runner.
        'TASKS': {
            'default': {'BACKEND': 'django_tasks_db.DatabaseBackend'},
        },

        'CACHES': {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            },
        },

        'MIDDLEWARE': [
            'django.middleware.common.CommonMiddleware',
            # Django's, plus an opt-out a view can set. SESSION_SAVE_EVERY_REQUEST
            # writes a session row on every request, and the Add page's progress poll
            # must not write at all: a writing poll queues behind the import it is
            # reporting on. See aledb_common.session_middleware.
            'aledb_common.session_middleware.SkipSaveSessionMiddleware',
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
        # BigAutoField, taken at the one moment it is free: every table is being created
        # from scratch, so there is no ALTER on every table and every foreign key to pay
        # for. Mutation ids are stored as bare integers in exported CSVs and in
        # aledb-phylogeny's branch_mutations, and widening the column changes none of
        # those values -- only how many of them there can eventually be.
        'DEFAULT_AUTO_FIELD': 'django.db.models.BigAutoField',

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
                        'aledb_common.context_processors.request_vocabulary',
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

    # Tests create and drop `test_<NAME>` on whatever server is configured. That used to be
    # harmless: the branch here swapped in SQLite, and Django substitutes an in-memory
    # database whatever NAME says. On PostgreSQL it is a real server, possibly a deployment's,
    # and the old branch cannot protect anything because there is nothing to swap to.
    #
    # So the guard is stated instead of implied. ALEDB_DB_MANAGED is set only by the entry
    # script, and only for a cluster it manages under env/ -- so running the suite against
    # somebody else's server has to be asked for in as many words.
    if 'test' in sys.argv or 'test_coverage' in sys.argv:
        if (os.environ.get('ALEDB_DB_MANAGED') != '1'
                and os.environ.get('ALEDB_ALLOW_REMOTE_TESTS') != '1'):
            raise ImproperlyConfigured(
                "Refusing to run tests against a database this checkout does not manage: "
                "they create and drop test_%s on it. Run them through ./aledb (or ./mutint, "
                "or ./aledb-deploy), which starts a local cluster under env/. Set "
                "ALEDB_ALLOW_REMOTE_TESTS=1 if you really mean this server."
                % settings['DATABASES']['default']['NAME'])

    return settings

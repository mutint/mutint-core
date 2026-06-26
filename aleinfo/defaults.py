import os
import sys
from pathlib import Path

from django.contrib import messages


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBUG = os.environ.get('DEBUG', '0') == '1'

GOOGLE_ANALYTICS_TAG = os.environ.get('GOOGLE_ANALYTICS_TAG', '')
SEQUENCING_URL = os.environ.get('SEQUENCING_URL', '')
ALE_DATA_ROOT_DIR = os.environ.get('ALE_DATA_ROOT_DIR', 'ale_data_root_dir')

ALLOWED_HOSTS = [os.environ.get('DJANGO_SERVER_HOST', 'localhost'), 'localhost', '127.0.0.1']
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 604800  # 1 week
SESSION_SAVE_EVERY_REQUEST = True

CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1:8000', 'http://localhost:8000']

INSTALLED_APPS = [
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
    # project apps (renamed to aledb_* — see Phase 5)
    'aledb_experiment',
    'aledb_import',
    'aledb_seq',
    'aledb_filter',
    'aledb_fixation',
    'aledb_stats',
    'aledb_metadata',
    'aledb_about',
    'aledb_converge',
    'aledb_accounts',
    'aledb_export',
    'aledb_common',
    'aledb_dashboard',
    'aledb_search',
    'aledb_genes',
    'aledb_bibliome',
    'aledb_home',
    'aledb_interop_query',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'aledb_local.sqlite3'),
    },
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

MIDDLEWARE = [
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'debug_toolbar.middleware.DebugToolbarMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'guardian.backends.ObjectPermissionBackend',
)

LOGIN_URL = '/accounts/login/'
ROOT_URLCONF = 'aleinfo.urls'
LOGIN_REDIRECT_URL = 'experiments_view'
LOGOUT_REDIRECT_URL = 'experiments_view'
WSGI_APPLICATION = 'aleinfo.wsgi.application'

USE_X_FORWARDED_PORT = os.environ.get('USE_X_FORWARDED_PORT', '0') == '1'

# Switch to SQLite for tests
if ('test' in sys.argv or 'test_coverage' in sys.argv or
        os.environ.get('FORCE_SQLITE') == '1'):
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'dev.sqlite3'),
    }

TIME_ZONE = 'America/Los_Angeles'
LANGUAGE_CODE = 'en-us'
SITE_ID = 1
USE_I18N = True
USE_L10N = True
USE_TZ = True

MEDIA_ROOT = ''
MEDIA_URL = ''

STATIC_ROOT = os.path.join(BASE_DIR, 'static')
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'aledb_common/staticfiles/'),
]
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
)

GUARDIAN_RAISE_403 = True
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'local-dev-only-insecure-key-change-in-production')

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
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
                'aleinfo.context_processors.global_settings',
            ],
            'debug': DEBUG,
        },
    },
]

DEFAULT_EXCEPTION_REPORTER_FILTER = 'django.views.debug.SafeExceptionReporterFilter'

LOGGING = {
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
        '': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'django': {
            'level': 'ERROR',
            'handlers': ['console'],
            'propagate': False,
        },
    },
}

PUBLIC = os.environ.get('PUBLIC', '0') == '1'
PUBLIC_USERNAME = os.environ.get('PUBLIC_USERNAME', 'public')
PUBLIC_PASSWORD = os.environ.get('PUBLIC_PASSWORD', 'REDACTED-CREDENTIAL')

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 9},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

MESSAGE_TAGS = {messages.ERROR: 'danger'}

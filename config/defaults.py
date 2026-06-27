"""
Standalone aledb-core base settings.
Delegates to aledb_common.base_settings so assembled projects can import
the same settings without touching the config/ namespace.
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from aledb_common.base_settings import get_base_settings as _get_base_settings

# Spread all settings into this module's namespace so `from config.defaults import *`
# continues to work exactly as before.
globals().update(_get_base_settings(BASE_DIR))

# These are project-specific and must not live in get_base_settings() —
# assembled projects override them with their own config package values.
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

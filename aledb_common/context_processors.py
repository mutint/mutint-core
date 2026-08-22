from django.conf import settings

from aledb_common.version import __version__


def branding(request):
    """Two separate things, deliberately.

    `branding` is the deployment's identity -- its name, version and logo. It is
    whatever ALEDB_BRANDING says and is empty by default, so an unconfigured
    aledb-core renders no name and no icon at all.

    `aledb_version` is aledb-core's own version, which is not configurable: it
    feeds the "Powered by ALEdb" watermark that every deployment carries.
    """
    return {
        'branding': getattr(settings, 'ALEDB_BRANDING', {}),
        'aledb_version': __version__,
    }


def global_settings(request):
    return {
        'GOOGLE_ANALYTICS_TAG': settings.GOOGLE_ANALYTICS_TAG,
    }


def plugin_exports(request):
    from aledb_common.plugin_registry import get_export_types
    return {
        'plugin_export_types': get_export_types(),
    }


def nav_items(request):
    from aledb_common.nav_registry import (
        EXPERIMENT_SECTION, MAIN_SECTION, get_nav_items,
    )
    return {
        'nav_main_items': get_nav_items(MAIN_SECTION),
        'nav_experiment_items': get_nav_items(EXPERIMENT_SECTION),
    }

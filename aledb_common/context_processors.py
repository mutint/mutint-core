from django.conf import settings


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

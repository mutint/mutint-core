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

from django.urls import include, re_path
from django.contrib import admin
from aledb_common.plugin_registry import get_plugin_urlpatterns


def get_core_urlpatterns():
    """Return the full list of URL patterns provided by aledb-core apps.

    Assembled projects import this instead of duplicating the pattern list:

        from aledb_common.urls import get_core_urlpatterns
        urlpatterns = get_core_urlpatterns() + [path('myapp/', include('myapp.urls'))]
    """
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
        re_path(r'^export', include('aledb_export.urls')),
        re_path(r'^filter/', include('aledb_filter.urls')),
        re_path(r'^import/', include('aledb_import.urls')),
        re_path(r'^interop-query/', include('aledb_interop_query.urls')),
        re_path(r'^metadata/', include('aledb_metadata.urls')),
        re_path(r'^mutations/', include('aledb_seq.urls')),
        re_path(r'^search/', include('aledb_search.urls')),
        re_path(r'^stats/', include('aledb_stats.urls')),
        re_path(r'^aledata/(?P<page_name>.*)$', protected_file_serve),
    ]

    urlpatterns += get_plugin_urlpatterns()
    return urlpatterns

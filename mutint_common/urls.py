from django.urls import include, re_path
from django.contrib import admin
from mutint_common.plugin_registry import get_plugin_urlpatterns


def get_core_urlpatterns():
    """Return the full list of URL patterns provided by mutint-core apps.

    Assembled projects import this instead of duplicating the pattern list:

        from mutint_common.urls import get_core_urlpatterns
        urlpatterns = get_core_urlpatterns() + [path('myapp/', include('myapp.urls'))]
    """
    from django.apps import apps as django_apps

    _auth_cfg = next(
        (cfg for cfg in django_apps.get_app_configs() if getattr(cfg, 'auth_app', False)),
        None,
    )

    urlpatterns = [
        re_path(r'^', include('mutint_home.urls')),
        re_path(r'^dashboard', include('mutint_dashboard.urls')),
        re_path(r'^admin/', admin.site.urls),
    ]

    if _auth_cfg:
        urlpatterns += [
            re_path(r'^accounts/', include(f'{_auth_cfg.name}.urls', namespace='accounts'))
        ]

    urlpatterns += [
        re_path(r'^about', include('mutint_about.urls')),
        # Four subjects that shared one `^ale/` prefix, each at its own. Projects and
        # groups were never about ALEs at all; the experiment and the sample are, and are
        # still two different things to address.
        re_path(r'^experiment/', include('mutint_experiment.experiment_urls')),
        re_path(r'^sample/', include('mutint_experiment.sample_urls')),
        re_path(r'^project/', include('mutint_experiment.project_urls')),
        re_path(r'^group/', include('mutint_experiment.group_urls')),
        re_path(r'^bibliome/', include('mutint_bibliome.urls')),
        re_path(r'^export/', include('mutint_export.urls')),
        re_path(r'^import/', include('mutint_import.urls')),
        re_path(r'^jobs/', include('mutint_jobs.urls')),
        re_path(r'^interop-query/', include('mutint_interop_query.urls')),
        re_path(r'^mutation-editor/', include('mutint_mutation_editor.urls')),
        re_path(r'^mutations/', include('mutint_sample.urls')),
        # Curation actions every mutation table posts to -- Compare, Fixed Mutations,
        # Converged Mutations and Search. Not under ^mutations/, which names only one
        # of those pages, and no longer even that one.
        re_path(r'^mutation-table/', include('mutint_sample.table_urls')),
        re_path(r'^search/', include('mutint_search.urls')),
        re_path(r'^stats/', include('mutint_stats.urls')),
    ]

    urlpatterns += get_plugin_urlpatterns()
    return urlpatterns

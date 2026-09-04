"""Routes for one sample, mounted at ``/sample/``.

Its own prefix rather than a branch of the experiment's, because a sample is addressed by
its own primary key and nothing above it -- the store is keyed the same way, and both pages
resolve the experiment by traversal. The experiment's *list* of samples is the experiment's
page and lives in `experiment_urls`.
"""

from django.urls import re_path

from aledb_experiment.sample_views import sample_edit, sample_update

urlpatterns = [
    re_path(r'^(?P<pk>[0-9]+)/edit/$', sample_edit, name="sample_edit"),
    re_path(r'^(?P<pk>[0-9]+)/update/$', sample_update, name="sample_update"),
]

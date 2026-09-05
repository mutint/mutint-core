"""Routes for a project, mounted at ``/project/``.

Out from under `/ale/` because a project is not about ALEs: it owns experiments, holds every
access grant, and is the only thing in the suite anything is *shared* on. It sat there
because one urlconf held four subjects.

Anchored throughout -- see `experiment_urls` for what the old unanchored `^projects` cost.
"""

from django.urls import re_path

from mutint_experiment.access_views import (
    project_access, project_access_bulk, project_access_grant, project_access_revoke,
)
from mutint_experiment.views import (
    project_create, project_delete, project_detail, project_edit, project_new,
    project_update, projects,
)

urlpatterns = [
    re_path(r'^$', projects, name="projects_view"),
    re_path(r'^new/$', project_new, name="project_new"),
    re_path(r'^create/$', project_create, name="project_create"),

    re_path(r'^(?P<pk>[0-9]+)/$', project_detail, name="project_detail"),
    re_path(r'^(?P<pk>[0-9]+)/edit/$', project_edit, name="project_edit"),
    re_path(r'^(?P<pk>[0-9]+)/update/$', project_update, name="project_update"),
    re_path(r'^(?P<pk>[0-9]+)/delete/$', project_delete, name="project_delete"),

    # Sharing: four ordered roles granted to a user or a group, on a project and nowhere
    # else. See `mutint_experiment/permissions.py`.
    re_path(r'^(?P<pk>[0-9]+)/access/$', project_access, name="project_access"),
    re_path(r'^(?P<pk>[0-9]+)/access/grant/$',
            project_access_grant, name="project_access_grant"),
    re_path(r'^(?P<pk>[0-9]+)/access/revoke/$',
            project_access_revoke, name="project_access_revoke"),
    re_path(r'^(?P<pk>[0-9]+)/access/bulk/$',
            project_access_bulk, name="project_access_bulk"),
]

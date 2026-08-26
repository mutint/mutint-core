from django.urls import include, re_path
from aledb_experiment.sample_views import (
    experiment_samples, experiment_samples_update, sample_edit, sample_update,
)
from aledb_experiment.access_views import (
    project_access, project_access_grant, project_access_revoke,
)
from aledb_experiment.group_views import (
    group_create, group_delete, group_detail, group_member_add, group_member_remove,
    group_member_update, group_new, group_transfer, group_update, groups,
)
from aledb_experiment.views import (
    experiment_create, experiment_delete, experiment_detail, experiment_edit,
    experiment_new, experiment_update, experiments,
    project_create, project_delete, project_detail, project_edit, project_new,
    project_update, projects,
)

urlpatterns = [
    # These must precede ^projects / ^experiments below: those patterns have no `$`, so
    # they are prefix matches that would otherwise swallow /ale/projects/create/.
    re_path(r'^projects/new/$', project_new, name="project_new"),
    re_path(r'^experiments/new/$', experiment_new, name="experiment_new"),
    re_path(r'^projects/create/$', project_create, name="project_create"),
    re_path(r'^experiments/create/$', experiment_create, name="experiment_create"),
    re_path(r'^project/(?P<pk>[0-9]+)/delete/$', project_delete, name="project_delete"),
    re_path(r'^experiment/(?P<pk>[0-9]+)/delete/$', experiment_delete, name="experiment_delete"),

    # Editing. `experiment/<pk>/samples/` is singular on purpose: a plural
    # `experiments/<pk>/samples/` would be swallowed by the `^experiments` prefix match
    # below, the same way `^projects` would swallow `projects/create/`.
    re_path(r'^project/(?P<pk>[0-9]+)/edit/$', project_edit, name="project_edit"),
    re_path(r'^project/(?P<pk>[0-9]+)/update/$', project_update, name="project_update"),
    re_path(r'^experiment/(?P<pk>[0-9]+)/edit/$', experiment_edit, name="experiment_edit"),
    re_path(r'^experiment/(?P<pk>[0-9]+)/update/$', experiment_update, name="experiment_update"),
    re_path(r'^sample/(?P<pk>[0-9]+)/edit/$', sample_edit, name="sample_edit"),
    re_path(r'^sample/(?P<pk>[0-9]+)/update/$', sample_update, name="sample_update"),
    re_path(r'^experiment/(?P<pk>[0-9]+)/samples/$',
            experiment_samples, name="experiment_samples"),
    re_path(r'^experiment/(?P<pk>[0-9]+)/samples/update/$',
            experiment_samples_update, name="experiment_samples_update"),

    # Sharing. Every route here is anchored, so none of them can grow into a prefix trap
    # of the kind `^projects` / `^experiments` below already are.
    re_path(r'^project/(?P<pk>[0-9]+)/access/$', project_access, name="project_access"),
    re_path(r'^project/(?P<pk>[0-9]+)/access/grant/$',
            project_access_grant, name="project_access_grant"),
    re_path(r'^project/(?P<pk>[0-9]+)/access/revoke/$',
            project_access_revoke, name="project_access_revoke"),

    # Groups. `^groups/` and `^group/` mirror the `^projects` / `^project/` split, but both
    # are anchored, so the specific-before-general ordering that `projects/create/` needs
    # does not apply -- they are kept in that order to match the file's shape anyway.
    re_path(r'^groups/new/$', group_new, name="group_new"),
    re_path(r'^groups/create/$', group_create, name="group_create"),
    re_path(r'^groups/$', groups, name="groups_view"),
    re_path(r'^group/(?P<pk>[0-9]+)/update/$', group_update, name="group_update"),
    re_path(r'^group/(?P<pk>[0-9]+)/delete/$', group_delete, name="group_delete"),
    re_path(r'^group/(?P<pk>[0-9]+)/transfer/$', group_transfer, name="group_transfer"),
    re_path(r'^group/(?P<pk>[0-9]+)/members/add/$',
            group_member_add, name="group_member_add"),
    re_path(r'^group/(?P<pk>[0-9]+)/members/update/$',
            group_member_update, name="group_member_update"),
    re_path(r'^group/(?P<pk>[0-9]+)/members/remove/$',
            group_member_remove, name="group_member_remove"),
    re_path(r'^group/(?P<pk>[0-9]+)/$', group_detail, name="group_detail"),

    re_path(r'^projects', projects, name="projects_view"),
    re_path(r'^experiments', experiments, name="experiments_view"),
    re_path(r"^project/(?P<pk>[0-9]+)/$", project_detail, name="project_detail"),
    re_path(r"^experiment/(?P<pk>[0-9]+)/$", experiment_detail, name="experiment_detail"),
]
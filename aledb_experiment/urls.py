from django.urls import include, re_path
from aledb_experiment.sample_views import (
    experiment_samples, experiment_samples_update, sample_edit, sample_update,
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

    re_path(r'^projects', projects, name="projects_view"),
    re_path(r'^experiments', experiments, name="experiments_view"),
    re_path(r"^project/(?P<pk>[0-9]+)/$", project_detail, name="project_detail"),
    re_path(r"^experiment/(?P<pk>[0-9]+)/$", experiment_detail, name="experiment_detail"),
]
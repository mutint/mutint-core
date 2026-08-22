from django.urls import include, re_path
from aledb_experiment.views import (
    experiment_create, experiment_delete, experiment_detail, experiments,
    project_create, project_delete, project_detail, projects,
)

urlpatterns = [
    # These must precede ^projects / ^experiments below: those patterns have no `$`, so
    # they are prefix matches that would otherwise swallow /ale/projects/create/.
    re_path(r'^projects/create/$', project_create, name="project_create"),
    re_path(r'^experiments/create/$', experiment_create, name="experiment_create"),
    re_path(r'^project/(?P<pk>[0-9]+)/delete/$', project_delete, name="project_delete"),
    re_path(r'^experiment/(?P<pk>[0-9]+)/delete/$', experiment_delete, name="experiment_delete"),

    re_path(r'^projects', projects, name="projects_view"),
    re_path(r'^experiments', experiments, name="experiments_view"),
    re_path(r"^project/(?P<pk>[0-9]+)/$", project_detail, name="project_detail"),
    re_path(r"^experiment/(?P<pk>[0-9]+)/$", experiment_detail, name="experiment_detail"),
]
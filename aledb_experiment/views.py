from django.shortcuts import get_object_or_404, render
from aledb_experiment.models import live
from django.shortcuts import redirect
from .models import Project, AleExperiment
from .utils import get_user_projects, get_all_user_exps
from .permissions import can_edit_project, can_view_project
import logging

logger = logging.getLogger(__name__)


def projects(request):
    project_list = get_user_projects(request.user)
    template_name = "ale/projects.html"
    project_dic = {}
    for project in project_list:
        project_experiments = live(project.aleexperiment_set.all())
        dois = []
        for project_experiment in project_experiments:
            if project_experiment.doi is not None:
                experiment_dois = project_experiment.doi.split()
            else:
                experiment_dois = []
            dois = dois + experiment_dois
        project_dic[project] = list(set(dois))

    return render(request, template_name, {'project_dic': project_dic.items()})


def _editable_projects(user):
    """Projects the user may create an experiment under.

    Viewable is not enough: `experiment_create` gates on `can_edit_project`, so offering a
    project here that the POST would 403 on is just a slower error.
    """
    return [project for project in get_user_projects(user)
            if can_edit_project(user, project)]


def experiments(request):
    experiment_list = get_all_user_exps(request.user)
    template_name = "ale/experiments.html"
    return render(request, template_name, {
        'experiments': experiment_list,
        'editable_projects': _editable_projects(request.user),
    })


def project_detail(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if can_view_project(request.user, project):
        experiments = live(project.aleexperiment_set.all())
        return render(request, "ale/project_detail.html", {
            "project": project,
            "experiments": experiments,
            "can_edit": can_edit_project(request.user, project),
        })
    return render(request, "403.html")


def experiment_detail(request, pk):
    # experiment = get_object_or_404(AleExperiment, pk=pk)
    # context = {
    #     "ale_experiment_id": experiment.ale_id,
    #     "ale_experiment_name": experiment.name,
    # }
    url = "/stats?ale_experiment_id="+pk
    return redirect(url)





# --- create / delete ---------------------------------------------------------------------
#
# Creation used to happen only as a side effect of uploading, and deletion only from the CLI
# or /admin/. These give both a home under the objects they act on.
#
# Deletion is soft: the row is flagged with a timestamp and the acting user, and
# `purge_deleted` removes it for real once the retention window passes.

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .permissions import can_delete_experiment, can_edit_project, grant_access_to_project


@require_POST
def project_create(request):
    """Create a project, optionally with its first experiment in the same step."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "A project name is required."}, status=400)

    project = Project.objects.create(
        name=name,
        user=request.user,
        is_public=bool(request.POST.get("is_public")),
        status="in progress",
        description=(request.POST.get("description") or "").strip())
    grant_access_to_project(project, [request.user])

    payload = {"project_id": project.id, "project": project.name, "experiment_id": None}

    experiment_name = (request.POST.get("experiment") or "").strip()
    if experiment_name:
        experiment = _create_experiment(project, experiment_name, request.user)
        payload["experiment_id"] = experiment.ale_id
        payload["experiment"] = experiment.name
    return JsonResponse(payload)


@require_POST
def experiment_create(request):
    """Create an experiment under an existing project."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "An experiment name is required."}, status=400)

    project = get_object_or_404(Project, pk=request.POST.get("project"))
    if not can_edit_project(request.user, project):
        return JsonResponse({"error": "You cannot add to this project."}, status=403)

    experiment = _create_experiment(project, name, request.user)
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "experiment": experiment.name,
                         "project_id": project.id})


def _create_experiment(project, name, user):
    """Experiments are identified by primary key, so a duplicate name is allowed."""
    import aledb_metadata.parser as metadata_defaults
    from aledb_experiment.models import Instrument

    instrument, _ = Instrument.objects.get_or_create(
        name=metadata_defaults.DEFAULT_INSTRUMENT_NAME)
    return AleExperiment.objects.create(
        name=name, project=project, instrument=instrument, person=user.get_username())


@require_POST
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if not can_edit_project(request.user, project):
        return JsonResponse({"error": "You cannot delete this project."}, status=403)
    if project.deleted_at is None:
        project.soft_delete(request.user)
    return JsonResponse({"project_id": project.id, "deleted_at": project.deleted_at})


@require_POST
def experiment_delete(request, pk):
    experiment = get_object_or_404(AleExperiment, pk=pk)
    if not can_delete_experiment(request.user, experiment):
        return JsonResponse({"error": "You cannot delete this experiment."}, status=403)
    if experiment.deleted_at is None:
        experiment.soft_delete(request.user)
    return JsonResponse({"experiment_id": experiment.ale_id,
                         "deleted_at": experiment.deleted_at})

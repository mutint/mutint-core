from django.contrib.auth.models import User, Group
from aledb_experiment.models import Project, AleExperiment, RecentExperiments, AleId, live
from django.core.exceptions import ObjectDoesNotExist
from aledb_experiment.permissions import can_view_project


def get_user_projects(user: User):
    """
    get projects based on user permissions
    :param user: the login user
    :return: list of projects that the user can view
    """
    if user.is_superuser:
        return live(Project.objects.all())

    # There was a short circuit here: when *no* project anywhere had a grant, this returned
    # every project. Combined with the stale app label -- which made that set always empty --
    # it is what showed every project to every user, anonymous ones included. It is not a
    # safe fallback even with the label fixed: a database whose grants have not been issued
    # yet would still hand out everything.
    return [project for project in live(Project.objects.all())
            if project.is_public or can_view_project(user, project)]


def get_all_user_exps(user):
    """
    Get all experiments that the user can view
    :param user: given login user
    :return: experiment queryset
    """
    projects = get_user_projects(user)
    return live(AleExperiment.objects.filter(project_id__in=projects)).order_by('name')


def _ale_exp_exists(ale_id, recent_experiments):
    try:
        recent_experiments.append(AleExperiment.objects.get(ale_id=ale_id))
    except ObjectDoesNotExist:
        pass
    return recent_experiments


def get_strains():
    """return list of sorted strains"""
    return sorted(
        AleId.objects.exclude(strain__isnull=True).exclude(strain='')
        .values_list('strain', flat=True).distinct()
    )


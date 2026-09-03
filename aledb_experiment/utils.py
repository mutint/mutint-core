from django.contrib.auth.models import User, Group
from aledb_experiment.models import Project, AleExperiment, AleId, live
from django.core.exceptions import ObjectDoesNotExist
from aledb_experiment.permissions import accessible_projects


def get_user_projects(user: User):
    """Projects the user may read, as a QuerySet.

    Thin now: `accessible_projects` answers this in one query. It used to walk every project
    in the database asking guardian about each one, and returned a QuerySet for superusers
    and a Python list for everyone else -- callers that iterated it twice paid twice.
    """
    return accessible_projects(user)


def get_all_user_exps(user):
    """
    Get all experiments that the user can view
    :param user: given login user
    :return: experiment queryset
    """
    return live(AleExperiment.objects.filter(
        project__in=get_user_projects(user))).order_by('name')


def _ale_exp_exists(ale_id, recent_experiments):
    try:
        recent_experiments.append(AleExperiment.objects.get(pk=ale_id))
    except ObjectDoesNotExist:
        pass
    return recent_experiments


def get_strains():
    """return list of sorted strains"""
    return sorted(
        AleId.objects.exclude(strain__isnull=True).exclude(strain='')
        .values_list('strain', flat=True).distinct()
    )


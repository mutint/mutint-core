from guardian.models import GroupObjectPermission, UserObjectPermission
from guardian.shortcuts import assign_perm

from aledb_experiment.models import AleExperiment
import logging

VIEW_PROJECT = 'view_project'

logger = logging.getLogger(__name__)


def _project_has_permissions(project, permission_codename):
    """
    Define if a project has permissions set
    :param project:
    :return:
    """
    if project.is_public:
        return False
    group_permissions = GroupObjectPermission.objects.filter(content_type__app_label='ale',
                                                             content_type__model='project',
                                                             object_pk=project.id,
                                                             permission__codename=permission_codename)
    if group_permissions:
        return True
    user_permissions = UserObjectPermission.objects.filter(content_type__app_label='ale',
                                                           content_type__model='project',
                                                           object_pk=project.id,
                                                           permission__codename=permission_codename)
    if user_permissions:
        return True
    return False


def get_users_with_access_to_project(project):
    users = set([])
    permissions = UserObjectPermission.objects.filter(object_pk=project.id)
    for permission in permissions:
        users.add(permission.user)
    return users


def grant_access_to_project(project, user_list):
    for user in user_list:
        assign_perm(VIEW_PROJECT, user, project)
    return


def can_view_project(user, project):
    ok = user.is_superuser or project.is_public or user.has_perm(VIEW_PROJECT, project)
    if not ok and user.is_staff:
        if _project_has_permissions(project, VIEW_PROJECT):
            ok = user.has_perm(VIEW_PROJECT, project)
        else:
            ok = True
    return ok


def can_edit_project(user, project):
    """Who may create under, or delete, a project.

    Deliberately built on `Project.user` and `is_superuser` rather than the guardian grant:
    the two `content_type__app_label='ale'` lookups in this module are stale (the app label is
    `aledb_experiment`), so they always return empty and cannot be trusted for a destructive
    action. `Project.user` is set at creation and is unambiguous.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return project is not None and project.user_id == user.id


def can_delete_experiment(user, experiment):
    """An experiment is deletable by whoever may edit the project holding it."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return can_edit_project(user, experiment.project) if experiment else False


def can_view_experiment(user, resequence_data_location):
    return True


def can_add_global_filter(user):
    return user.is_superuser


def can_add_experiment_filter(user, experiment):
    if experiment:
        return user.is_superuser or user.has_perm(VIEW_PROJECT, experiment.project)
    return False

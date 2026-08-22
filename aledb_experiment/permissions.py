from guardian.models import UserObjectPermission
from guardian.shortcuts import assign_perm

from aledb_experiment.models import AleExperiment
import logging

VIEW_PROJECT = 'view_project'

logger = logging.getLogger(__name__)


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
    """Superusers, public projects, anyone holding the guardian grant -- and all staff.

    `user.has_perm` goes through guardian's own backend, which resolves the content type
    from the instance, so that clause was always correct.

    The staff clause is a deliberate blanket grant. It used to be conditional -- staff were
    let through only on projects that had no grant at all -- but the query behind that test
    filtered on `content_type__app_label='ale'` while the label is `aledb_experiment`, so it
    matched nothing and staff were let through on *everything*. Correcting the label without
    also flattening this would have quietly reversed it, cutting staff off from every project
    the backfill migration granted. `load_projects` creates every imported user with
    `is_staff=True`, so that is most of the user base. Narrowing who counts as staff is a
    separate decision from fixing the lookup.
    """
    if user.is_superuser or project.is_public:
        return True
    if user.has_perm(VIEW_PROJECT, project):
        return True
    return bool(user.is_staff)


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


def can_add_global_filter(user):
    return user.is_superuser


def can_add_experiment_filter(user, experiment):
    if experiment:
        return user.is_superuser or user.has_perm(VIEW_PROJECT, experiment.project)
    return False

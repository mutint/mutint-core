"""The sharing page: who has what role on one project.

Split out of `views.py` the way `sample_views.py` was -- same shape as everything else here.
A GET page that checks permission itself and renders `403.html` with a status, plus
`@require_POST` JSON endpoints that check again, because the button being hidden is not a
permission check.

`project_access_grant` serves the "add a person"/"add a group" boxes **and** the per-row role
dropdown. It is an upsert keyed on the subject, so there is no separate change-role endpoint
that could drift from it.

Per-row endpoints rather than one bulk save. `experiment_samples_update` is bulk because
renumbering samples is inherently multi-row and a half-applied numbering is incoherent; access
changes are independent of each other and each guardrail has its own message to deliver.
"""

import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from aledb_common.util import get_user_context
from aledb_experiment.group_permissions import manageable_groups
from aledb_experiment.models import Project, ProjectAccess
from aledb_experiment.permissions import (
    AccessError, can_manage_project_access, can_own_project, effective_role,
    grant_project_access, resolve_group_name, resolve_username, revoke_project_access,
)
from aledb_experiment.roles import ROLE_CHOICES, ROLE_OWNER, is_role, rank

logger = logging.getLogger(__name__)


@ensure_csrf_cookie
def project_access(request, pk):
    """`/ale/project/<pk>/access/` -- the sharing page."""
    project = get_object_or_404(Project, pk=pk)
    context = get_user_context(request.user)
    if not can_manage_project_access(request.user, project):
        return render(request, "403.html", context, status=403)

    actor_role = effective_role(request.user, project)

    # An admin may grant read/write/admin but never owner, so the dropdowns offer what this
    # particular actor may hand out rather than the full list. A row they cannot re-grant --
    # an existing owner, seen by an admin -- renders as text instead of a dropdown, so the
    # page never presents a control whose every use the server would refuse.
    grantable = [(value, label) for value, label in ROLE_CHOICES
                 if rank(value) <= rank(actor_role)]
    grantable_to_group = [pair for pair in grantable if pair[0] != ROLE_OWNER]

    rows = []
    for entry in (ProjectAccess.objects.filter(project=project)
                  .select_related("user", "group")
                  .order_by("user__username", "group__name")):
        options = grantable_to_group if entry.group_id else grantable
        rows.append({
            "entry": entry,
            "editable": rank(entry.role) <= rank(actor_role),
            "options": options,
            "role_label": dict(ROLE_CHOICES).get(entry.role, entry.role),
        })

    context.update({
        "project": project,
        "rows": rows,
        "actor_role": actor_role,
        "grantable": grantable,
        "grantable_to_group": grantable_to_group,
        "can_grant_owner": can_own_project(request.user, project),
        "my_groups": manageable_groups(request.user),
    })
    return render(request, "ale/project_access.html", context)


@require_POST
def project_access_grant(request, pk):
    """Give a user or a group a role. Upserts, so it is also "change this row's role"."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    project = get_object_or_404(Project, pk=pk)
    if not can_manage_project_access(request.user, project):
        return JsonResponse({"error": "You cannot manage access to this project."},
                            status=403)

    username = (request.POST.get("username") or "").strip()
    group_name = (request.POST.get("group") or "").strip()
    if bool(username) == bool(group_name):
        return JsonResponse({"error": "Name either a user or a group, not both."},
                            status=400)

    role = (request.POST.get("role") or "").strip()
    if not is_role(role):
        return JsonResponse({"error": "Unknown role."}, status=400)

    # You cannot hand out more than you hold. This is what stops an admin making themselves
    # -- or anyone else -- an owner.
    actor_role = effective_role(request.user, project)
    if rank(role) > rank(actor_role):
        return JsonResponse({"error": "You cannot grant a role above your own."}, status=403)
    if role == ROLE_OWNER and not can_own_project(request.user, project):
        return JsonResponse({"error": "Only an owner can grant ownership."}, status=403)

    try:
        subject = (resolve_username(username) if username
                   else resolve_group_name(group_name, request.user))
        entry = grant_project_access(project, subject, role, granted_by=request.user)
    except AccessError as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)

    return JsonResponse({"project_id": project.id, "access_id": entry.id,
                         "kind": entry.subject_kind(), "subject": entry.subject_name(),
                         "role": entry.role})


@require_POST
def project_access_revoke(request, pk):
    """Remove one grant, named by its row id.

    By `access_id` rather than by username: unambiguous, and it cannot race with a rename or
    with the same person being added under a different spelling.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    project = get_object_or_404(Project, pk=pk)
    if not can_manage_project_access(request.user, project):
        return JsonResponse({"error": "You cannot manage access to this project."},
                            status=403)

    entry = ProjectAccess.objects.filter(project=project,
                                         pk=request.POST.get("access_id")).first()
    if entry is None:
        return JsonResponse({"error": "That grant no longer exists."}, status=404)

    if entry.role == ROLE_OWNER and not can_own_project(request.user, project):
        return JsonResponse({"error": "Only an owner can revoke ownership."}, status=403)

    try:
        revoke_project_access(project, entry)
    except AccessError as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)

    return JsonResponse({"access_id": entry.id, "removed": True,
                         "owner": project.user.get_username() if project.user_id else None})

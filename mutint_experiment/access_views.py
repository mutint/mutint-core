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

`project_access_bulk` does not overturn that. It exists because adding ten people was ten page
loads, each clearing the box you were typing in -- a cost paid in the browser, not a statement
about where the rules live. It **loops the same two helpers** every other endpoint calls and
reports one result per subject, so each guardrail still delivers its own message and nothing
here knows what the rules are.

Which makes it deliberately **partial**, unlike `experiment_samples_update`: it applies what it
can and names what it could not. That is the same reasoning read forwards -- a half-applied
sample renumbering is incoherent, a half-applied set of independent grants is just the subset
that was allowed.

The one genuine cross-row dependency is the last-owner invariant, which is why the loop applies
sequentially through `grant_project_access` rather than validating up front: `_remaining_owner_count`
reads the database each call, so a batch demoting two of two owners would pass every individual
check made against the starting state and leave the project ownerless.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from mutint_common.util import get_user_context
from mutint_experiment.group_permissions import manageable_groups
from mutint_experiment.models import Project, ProjectAccess
from mutint_experiment.permissions import (
    AccessError, can_manage_project_access, can_own_project, effective_role,
    grant_project_access, resolve_group_name, resolve_username, revoke_project_access,
    self_revoke_refusal,
)
from mutint_experiment.roles import ROLE_CHOICES, ROLE_OWNER, is_role, rank

logger = logging.getLogger(__name__)

#: A declared cap on one bulk apply, as `samples.MAX_ROWS` is. Not a performance limit -- the
#: loop is one upsert per subject -- but a bound on what a single malformed request can ask for.
MAX_BULK_SUBJECTS = 500


@ensure_csrf_cookie
def project_access(request, pk):
    """`/project/<pk>/access/` -- the sharing page."""
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
        editable = rank(entry.role) <= rank(actor_role)
        # Two separate questions, and the second is not a weaker form of the first: an owner
        # may edit their own row -- stepping down is how ownership is handed over -- while
        # `self_revoke_refusal` still refuses to let them remove it. The button is hidden
        # because a control whose every use the server refuses should not be on the page;
        # `project_access_revoke` asks the same question again, which is the check.
        rows.append({
            "entry": entry,
            "editable": editable,
            "is_self": entry.user_id == request.user.id,
            "removable": editable and not self_revoke_refusal(request.user, entry),
            "options": options,
            "role_label": dict(ROLE_CHOICES).get(entry.role, entry.role),
        })

    context.update({
        "project": project,
        "rows": rows,
        # The label rather than the stored value, because it is read by a person at the top
        # of the page: "Read/write", not "write". Same line the group page carries. The raw
        # `actor_role` went with the summary list at the foot that used to render it.
        "actor_role_label": dict(ROLE_CHOICES).get(actor_role, actor_role),
        "grantable": grantable,
        "grantable_to_group": grantable_to_group,
        "can_grant_owner": can_own_project(request.user, project),
        "my_groups": manageable_groups(request.user),
    })
    return render(request, "project/access.html", context)


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

    Removing *your own* grant is allowed -- leaving a project you are on does not need anyone
    else -- except as an owner, where `self_revoke_refusal` says why.
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

    refusal = self_revoke_refusal(request.user, entry)
    if refusal:
        return JsonResponse({"error": refusal}, status=403)

    try:
        revoke_project_access(project, entry)
    except AccessError as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)

    return JsonResponse({"access_id": entry.id, "removed": True,
                         "owner": project.user.get_username() if project.user_id else None})


@require_POST
def project_access_bulk(request, pk):
    """Apply one role to several existing grants, and/or add several people at once.

    Body: `access_ids` (JSON array of grant ids), `usernames` (newline-separated), and the
    `role` to give all of them. Answers `{applied, added, errors: {subject: message}}`.

    Partial by design -- see the module docstring. Every subject goes through
    `grant_project_access`, one at a time and in order, so the guardrails are the same ones
    the per-row endpoint enforces and the last-owner check sees each intermediate state.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    project = get_object_or_404(Project, pk=pk)
    if not can_manage_project_access(request.user, project):
        return JsonResponse({"error": "You cannot manage access to this project."},
                            status=403)

    role = (request.POST.get("role") or "").strip()
    if not is_role(role):
        return JsonResponse({"error": "Unknown role."}, status=400)

    actor_role = effective_role(request.user, project)
    if rank(role) > rank(actor_role):
        return JsonResponse({"error": "You cannot grant a role above your own."}, status=403)
    if role == ROLE_OWNER and not can_own_project(request.user, project):
        return JsonResponse({"error": "Only an owner can grant ownership."}, status=403)

    try:
        access_ids = json.loads(request.POST.get("access_ids") or "[]")
        if not isinstance(access_ids, list):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Malformed request: access_ids was not a list."},
                            status=400)

    usernames = [line.strip() for line in
                 (request.POST.get("usernames") or "").splitlines() if line.strip()]

    if not access_ids and not usernames:
        return JsonResponse({"error": "Select some rows, or name someone to add."},
                            status=400)
    if len(access_ids) + len(usernames) > MAX_BULK_SUBJECTS:
        return JsonResponse(
            {"error": "Too many at once (%d); the limit is %d."
                      % (len(access_ids) + len(usernames), MAX_BULK_SUBJECTS)}, status=400)

    subjects, errors = _bulk_subjects(project, actor_role, access_ids, usernames)

    applied = 0
    added = 0
    for label, subject, existing in subjects:
        try:
            grant_project_access(project, subject, role, granted_by=request.user)
        except AccessError as refusal:
            errors[label] = str(refusal)
            continue
        applied += 1
        if existing is None:
            added += 1

    return JsonResponse({"project_id": project.id, "applied": applied, "added": added,
                         "errors": errors})


def _bulk_subjects(project, actor_role, access_ids, usernames):
    """Resolve a batch to `[(label, subject, existing_entry_or_None), ...]` plus errors.

    Deduplicated by subject: `ProjectAccess` is conditionally unique on `(project, user)` and
    `(project, group)`, so the same person named twice -- once by a ticked row and once in the
    add box, which is the obvious way to do it by accident -- would otherwise be two upserts
    racing to be last.
    """
    subjects = []
    errors = {}
    seen = set()

    by_id = {entry.id: entry for entry in
             ProjectAccess.objects.filter(project=project).select_related("user", "group")}

    for raw in access_ids:
        # From this project's grants only, so an id from elsewhere simply is not here --
        # the same containment `parse_rows` gets from building its map per experiment.
        entry = by_id.get(int(raw)) if str(raw).lstrip("-").isdigit() else None
        if entry is None:
            errors[str(raw)] = "That grant no longer exists."
            continue
        label = entry.subject_name()
        if rank(entry.role) > rank(actor_role):
            # The page renders these rows as text rather than a dropdown; a bulk apply has to
            # skip them for the same reason instead of quietly including them.
            errors[label] = "You cannot change a grant above your own role."
            continue
        key = ("group", entry.group_id) if entry.group_id else ("user", entry.user_id)
        if key in seen:
            continue
        seen.add(key)
        subjects.append((label, entry.group or entry.user, entry))

    for name in usernames:
        try:
            user = resolve_username(name)
        except AccessError as refusal:
            errors[name] = str(refusal)
            continue
        key = ("user", user.id)
        if key in seen:
            continue
        seen.add(key)
        existing = next((e for e in by_id.values() if e.user_id == user.id), None)
        subjects.append((user.get_username(), user, existing))

    return subjects, errors

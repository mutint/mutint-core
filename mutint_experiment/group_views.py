"""Group pages: create a group, see who is in it, manage its members.

A group exists so that a whole lab can be given access to a project in one grant instead of
one row per person. Nothing here grants anything -- that is the sharing page's job. The two
vocabularies are deliberately disjoint (see `group_permissions.py`): being a project admin
gives you no standing in any group, and managing a group gives you no standing on any project.

Same house shape as the rest: function-based views, inline permission checks, GET pages that
render `403.html` with a status, `@require_POST` JSON endpoints posted to by `mutintPost`.
"""

import logging

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from mutint_common.util import get_user_context
from mutint_experiment.group_permissions import (
    is_group_manager, is_group_member, is_group_owner, visible_groups,
)
from mutint_experiment.models import UserGroup, UserGroupMembership, ProjectAccess
from mutint_experiment.permissions import AccessError, clear_role_cache, resolve_username

logger = logging.getLogger(__name__)


def _standing(user, group):
    if is_group_owner(user, group):
        return "Owner"
    if is_group_manager(user, group):
        return "Manager"
    return "Member"


def groups(request):
    """`/group/` -- the groups you own or belong to."""
    if not request.user.is_authenticated:
        return render(request, "403.html", get_user_context(request.user), status=403)

    context = get_user_context(request.user)
    listed = visible_groups(request.user).select_related("owner")
    context.update({
        "groups": [{"group": group,
                    "standing": _standing(request.user, group),
                    "members": group.member_count()}
                   for group in listed],
    })
    return render(request, "group/list.html", context)


def group_new(request):
    """The create-a-group form, on a page of its own -- as project and experiment are."""
    if not request.user.is_authenticated:
        return render(request, "403.html", get_user_context(request.user), status=403)
    return render(request, "group/new.html", get_user_context(request.user))


@require_POST
def group_create(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "A group name is required."}, status=400)
    if len(name) > 80:
        return JsonResponse({"error": "A group name is at most 80 characters."}, status=400)

    description = (request.POST.get("description") or "").strip()
    if len(description) > 300:
        return JsonResponse(
            {"error": "A group description is at most 300 characters."}, status=400)

    # Checked here as well as by the constraint, because an IntegrityError is not a sentence
    # anyone can act on. The constraint is what makes it true under a race.
    if UserGroup.objects.filter(name__iexact=name).exists():
        return JsonResponse({"error": 'There is already a group named "%s".' % name},
                            status=400)

    try:
        with transaction.atomic():
            group = UserGroup.objects.create(name=name, description=description,
                                            owner=request.user)
            # The owner holds a membership row too, so `group.memberships` is the complete
            # roster and the permission query reaches them through the same join as everyone
            # else -- no `Q(group__owner=user)` special case in the hot path.
            UserGroupMembership.objects.create(group=group, user=request.user,
                                              is_manager=True, added_by=request.user)
    except IntegrityError:
        return JsonResponse({"error": 'There is already a group named "%s".' % name},
                            status=400)

    return JsonResponse({"group_id": group.id, "name": group.name})


@ensure_csrf_cookie
def group_detail(request, pk):
    group = get_object_or_404(UserGroup, pk=pk)
    context = get_user_context(request.user)
    if not is_group_member(request.user, group):
        return render(request, "403.html", context, status=403)

    context.update({
        "group": group,
        "memberships": (group.memberships.select_related("user")
                        .order_by("-is_manager", "user__username")),
        "owner_id": group.owner_id,
        "can_manage": is_group_manager(request.user, group),
        "is_owner": is_group_owner(request.user, group),
        # The same word the groups list puts in its Standing column, so a group reads the
        # same way from either page.
        "standing": _standing(request.user, group),
        # What removing someone actually costs them. A manager deciding whether to take
        # somebody out of a group should be able to see what that takes away.
        "shared_projects": (ProjectAccess.objects.filter(group=group)
                            .select_related("project").order_by("project__name")),
    })
    return render(request, "group/detail.html", context)


@require_POST
def group_update(request, pk):
    group = get_object_or_404(UserGroup, pk=pk)
    if not is_group_manager(request.user, group):
        return JsonResponse({"error": "You cannot edit this group."}, status=403)

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "A group name is required."}, status=400)
    if len(name) > 80:
        return JsonResponse({"error": "A group name is at most 80 characters."}, status=400)

    description = (request.POST.get("description") or "").strip()
    if len(description) > 300:
        return JsonResponse(
            {"error": "A group description is at most 300 characters."}, status=400)

    if UserGroup.objects.filter(name__iexact=name).exclude(pk=group.pk).exists():
        return JsonResponse({"error": 'There is already a group named "%s".' % name},
                            status=400)

    group.name = name
    group.description = description
    try:
        group.save(update_fields=["name", "description"])
    except IntegrityError:
        return JsonResponse({"error": 'There is already a group named "%s".' % name},
                            status=400)
    return JsonResponse({"group_id": group.id, "name": group.name})


@require_POST
def group_member_add(request, pk):
    group = get_object_or_404(UserGroup, pk=pk)
    if not is_group_manager(request.user, group):
        return JsonResponse({"error": "You cannot add members to this group."}, status=403)

    wants_manager = bool(request.POST.get("is_manager"))
    if wants_manager and not is_group_owner(request.user, group):
        return JsonResponse({"error": "Only the group's owner can appoint a manager."},
                            status=403)

    try:
        user = resolve_username(request.POST.get("username"))
    except AccessError as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)

    if UserGroupMembership.objects.filter(group=group, user=user).exists():
        # Not a silent no-op: someone typed a name and is owed an answer about it.
        return JsonResponse({"error": "%s is already in this group." % user.get_username()},
                            status=400)

    membership = UserGroupMembership.objects.create(
        group=group, user=user, is_manager=wants_manager, added_by=request.user)
    clear_role_cache()   # the new member may now reach projects shared with this group
    return JsonResponse({"membership_id": membership.id, "username": user.get_username(),
                         "is_manager": membership.is_manager})


@require_POST
def group_member_update(request, pk):
    """Appoint or demote a manager. Owner only."""
    group = get_object_or_404(UserGroup, pk=pk)
    if not is_group_owner(request.user, group):
        return JsonResponse({"error": "Only the group's owner can change a member's role."},
                            status=403)

    membership = UserGroupMembership.objects.filter(
        group=group, pk=request.POST.get("membership_id")).first()
    if membership is None:
        return JsonResponse({"error": "That member is no longer in this group."}, status=404)
    if membership.user_id == group.owner_id:
        return JsonResponse({"error": "The group's owner is always a manager."}, status=400)

    membership.is_manager = bool(request.POST.get("is_manager"))
    membership.save(update_fields=["is_manager"])
    return JsonResponse({"membership_id": membership.id,
                         "is_manager": membership.is_manager})


@require_POST
def group_member_remove(request, pk):
    group = get_object_or_404(UserGroup, pk=pk)
    if not is_group_manager(request.user, group):
        return JsonResponse({"error": "You cannot remove members from this group."},
                            status=403)

    membership = UserGroupMembership.objects.filter(
        group=group, pk=request.POST.get("membership_id")).first()
    if membership is None:
        return JsonResponse({"error": "That member is no longer in this group."}, status=404)

    if membership.user_id == group.owner_id:
        return JsonResponse({"error": "The group's owner cannot be removed; transfer the "
                                      "group first."}, status=400)
    if membership.is_manager and not is_group_owner(request.user, group):
        return JsonResponse({"error": "Only the group's owner can remove a manager."},
                            status=403)

    membership.delete()
    clear_role_cache()   # they may have just lost access to projects shared with this group
    return JsonResponse({"membership_id": int(request.POST["membership_id"]),
                         "removed": True})


@require_POST
def group_delete(request, pk):
    """Owner only. Cascades the memberships and every project grant the group held."""
    group = get_object_or_404(UserGroup, pk=pk)
    if not is_group_owner(request.user, group):
        return JsonResponse({"error": "Only the group's owner can delete it."}, status=403)

    lost = ProjectAccess.objects.filter(group=group).count()
    group_id = group.id
    group.delete()
    clear_role_cache()
    return JsonResponse({"group_id": group_id, "deleted": True, "projects_affected": lost})


@require_POST
def group_transfer(request, pk):
    """Hand the group to another member. Owner only.

    The old owner stays on as a manager rather than being ejected: transferring is usually
    someone leaving a role, not leaving the lab, and a transfer that silently removed them
    would be a surprising way to lose access to every project the group reaches.
    """
    group = get_object_or_404(UserGroup, pk=pk)
    if not is_group_owner(request.user, group):
        return JsonResponse({"error": "Only the group's owner can transfer it."}, status=403)

    try:
        user = resolve_username(request.POST.get("username"))
    except AccessError as refusal:
        return JsonResponse({"error": str(refusal)}, status=400)

    if user.id == group.owner_id:
        return JsonResponse({"error": "%s already owns this group."
                                      % user.get_username()}, status=400)

    with transaction.atomic():
        previous_owner_id = group.owner_id
        group.owner = user
        group.save(update_fields=["owner"])
        # The new owner must hold a manager membership; the outgoing one keeps theirs.
        UserGroupMembership.objects.update_or_create(
            group=group, user=user,
            defaults={"is_manager": True, "added_by": request.user})
        UserGroupMembership.objects.filter(
            group=group, user_id=previous_owner_id).update(is_manager=True)

    clear_role_cache()
    return JsonResponse({"group_id": group.id, "owner": user.get_username()})

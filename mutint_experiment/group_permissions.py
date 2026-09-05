"""Who may do what to a group.

Kept apart from `permissions.py` on purpose. Project roles and group roles are disjoint
vocabularies with no bridge between them, and mixing them in one module is how you end up
with `can_edit_project(user, group)` typed by accident and passing.

The absence of a bridge is itself a guardrail. Holding `admin` on a project lets you add a
group to it; it gives you nothing at all over that group. You can grant "Barrick Lab" access
to your project without being able to add yourself to "Barrick Lab" -- which is the
escalation someone will look for.

Three standings, and the owner holds all of them:

- **owner**  -- one person, `UserGroup.owner`. Appoints managers, transfers, deletes.
- **manager** -- a membership row with `is_manager`. Renames the group and manages plain
  members, but cannot touch another manager or the owner.
- **member** -- a membership row. Sees the group page; that is all.
"""

from django.db.models import Q

from mutint_experiment.models import UserGroup, UserGroupMembership


def _real(user):
    return bool(user and getattr(user, "is_authenticated", False))


def group_membership(user, group):
    if not _real(user):
        return None
    return UserGroupMembership.objects.filter(group=group, user=user).first()


def is_group_owner(user, group):
    if not _real(user):
        return False
    return bool(user.is_superuser or group.owner_id == user.id)


def is_group_manager(user, group):
    if is_group_owner(user, group):
        return True
    membership = group_membership(user, group)
    return bool(membership and membership.is_manager)


def is_group_member(user, group):
    if is_group_owner(user, group):
        return True
    return group_membership(user, group) is not None


def visible_groups(user):
    """Groups `user` owns or belongs to. What the add-a-group box resolves against."""
    if not _real(user):
        return UserGroup.objects.none()
    if user.is_superuser:
        return UserGroup.objects.all()
    # `owner` as well as the membership row: the owner always has one, but a group that
    # somehow lost it must not become invisible to the only person who can repair it.
    return UserGroup.objects.filter(Q(memberships__user=user) | Q(owner=user)).distinct()


def manageable_groups(user):
    """Groups `user` owns or manages."""
    if not _real(user):
        return UserGroup.objects.none()
    if user.is_superuser:
        return UserGroup.objects.all()
    return UserGroup.objects.filter(
        Q(memberships__user=user, memberships__is_manager=True) | Q(owner=user)).distinct()

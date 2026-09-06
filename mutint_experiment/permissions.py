"""Who may do what to a project. The whole policy lives here.

Everything below the project inherits: an experiment, a sample and a mutation are all
reached through `experiment.project`, so `can_edit_project(user, experiment.project)` is the
only question any of them asks. Nothing is owned at the experiment or sample level.

Four roles, ordered (`mutint_experiment/roles.py`):

    read  < write < admin < owner

`read` sees the project and its data. `write` adds, edits and curates -- data, samples, their
flags and mutations. `admin` additionally manages who has access and may soft-delete the
project. `owner` additionally grants and revokes ownership.

Three things confer a role without a `ProjectAccess` row, and all three are in
`effective_role`: a superuser is owner everywhere; `Project.user` is owner of their own
project; and `is_public` gives everyone, signed in or not, `read`.

**There is no blanket grant for staff.** `can_view_project` used to end
`return bool(user.is_staff)`, and `load_projects` creates every imported user with
`is_staff=True`, so on a real deployment that made every project readable by almost everyone
and every role below `admin` decorative. Restoring it would empty this module of meaning; the
escape hatch for a deployment that relied on it is `./mutint project_access`.
"""

import logging

from django.db.models import Q

from mutint_experiment.models import UserGroup, Project, ProjectAccess, live
from mutint_experiment.roles import (
    ROLE_ADMIN, ROLE_OWNER, ROLE_READ, ROLE_WRITE,
    at_least, best_role, is_role, rank, roles_at_least,
)

logger = logging.getLogger(__name__)


class AccessError(Exception):
    """A refusal whose message is written for a person and is safe to show verbatim."""


class ExperimentLocked(Exception):
    """A write was refused because the experiment is locked, not because of who asked.

    Lives here rather than with any one write path because the lock is policy, and because
    both `mutint_common.import_registry` and `mutint_curate.history` raise it -- two
    layers that share nothing else. Its message is `Experiment.lock_message()`, written
    for a person and safe to show verbatim.
    """


# --- the per-request role cache -----------------------------------------------------------
#
# This is not an optimisation, it is what keeps a mutation table from issuing a query per row.
# The old cross-sample table's header builder called can_add_experiment_filter once per sample
# column and `get_mutation_table_body` once per mutation row -- a table of 400 mutations
# across 20 samples asks 420 times. django-guardian used to absorb that in its own
# `_guardian_perm_cache` on the user object; replacing it with a plain lookup and no cache
# would have been a silent, large regression on the busiest page in the product.
#
# The cache hangs off the `User` instance, so its lifetime is naturally one request --
# `request.user` is one object per request and is discarded with it. A module-level
# generation counter, bumped by every write helper below, invalidates it: that matters most
# in tests and in the CLI, where one `User` object outlives several grants and would
# otherwise keep answering with the role it had at the start.

_CACHE_ATTR = "_mutint_project_roles"
_generation = 0


def _bump_cache():
    global _generation
    _generation += 1


def clear_role_cache(user=None):
    """Drop cached roles. Bumping the generation invalidates every user's at once."""
    _bump_cache()
    if user is not None:
        try:
            delattr(user, _CACHE_ATTR)
        except AttributeError:
            pass


def _cached(user, project_pk):
    entry = getattr(user, _CACHE_ATTR, {}).get(project_pk)
    if entry is None or entry[0] != _generation:
        return None, False
    return entry[1], True


def _remember(user, project_pk, role):
    try:
        cache = user.__dict__.setdefault(_CACHE_ATTR, {})
    except AttributeError:
        # AnonymousUser and friends: not worth caching, and not worth failing over.
        return role
    cache[project_pk] = (_generation, role)
    return role


# --- reading the policy -------------------------------------------------------------------


def effective_role(user, project):
    """The best role `user` holds on `project`, or None if they hold none.

    One query, cached. Every `can_*` predicate below is a comparison against this.
    """
    if project is None:
        return None

    if user is None or not getattr(user, "is_authenticated", False):
        return ROLE_READ if project.is_public else None

    if user.is_superuser:
        return ROLE_OWNER

    cached, hit = _cached(user, project.pk)
    if hit:
        return cached

    role = ROLE_READ if project.is_public else None
    if project.user_id == user.id:
        # The project names them. Deliberately not dependent on the mirror ProjectAccess row
        # existing: a missing mirror is then a display bug rather than an owner locked out of
        # their own project, which is exactly the trap the old scheme had.
        role = ROLE_OWNER
    else:
        granted = ProjectAccess.objects.filter(
            Q(user=user) | Q(group__memberships__user=user),
            project=project,
        ).values_list("role", flat=True)
        for grant in granted:
            role = best_role(role, grant)

    return _remember(user, project.pk, role)


def has_project_role(user, project, minimum):
    return at_least(effective_role(user, project), minimum)


def can_view_project(user, project):
    return has_project_role(user, project, ROLE_READ)


def can_edit_project(user, project):
    """Add, edit and curate. Was "owner or superuser"; collaboration is the point here."""
    return has_project_role(user, project, ROLE_WRITE)


def can_admin_project(user, project):
    return has_project_role(user, project, ROLE_ADMIN)


def can_own_project(user, project):
    return has_project_role(user, project, ROLE_OWNER)


def can_manage_project_access(user, project):
    """Who may open /project/<pk>/access/ and change what is on it."""
    return has_project_role(user, project, ROLE_ADMIN)


def can_delete_project(user, project):
    """Deliberately admin, and deliberately *not* `can_edit_project`.

    Widening editing to `write` would otherwise have handed every read/write collaborator the
    ability to soft-delete the whole project, which is not what "may add data" means to
    anyone. Admin rather than owner because deletion is soft and `purge_deleted` gives a
    retention window, so it is recoverable.
    """
    return has_project_role(user, project, ROLE_ADMIN)


def can_edit_experiment(user, experiment):
    """Write access to an experiment's data -- **and** the experiment not being locked.

    The lock is not a fifth role. It answers a different question: not "who are you" but
    "is this dataset still open", and it outranks the first. A locked experiment refuses
    everyone, including admins, owners and superusers; an admin unlocks it, edits, and locks
    it again. That is the whole point -- a permission tier cannot protect a finished dataset
    from someone who legitimately has permission and did not mean to touch it.

    Every web write that acts on an experiment should ask this rather than
    `can_edit_project(user, experiment.project)`, which cannot see the lock because it is
    handed the project and the flag is on the experiment.

    **Not** asked by the rebuild registry. Recomputing derived data is not editing: the
    numbers on the Overview are a function of the mutations, and a locked experiment whose
    counts silently went stale because nobody was allowed to refresh them would be worse than
    one that keeps up.
    """
    if experiment is None:
        return False
    if experiment.is_locked:
        return False
    return can_edit_project(user, experiment.project)


def experiment_lock_refusal(experiment):
    """The message for a write refused by the lock, or "" if the lock is not why.

    Lets a caller tell the two refusals apart: "you may not edit this" and "nobody may edit
    this at the moment" are different answers and deserve different words.
    """
    if experiment is not None and experiment.is_locked:
        return experiment.lock_message()
    return ""


def can_lock_experiment(user, experiment):
    """Who may lock or unlock. Admin on the project, mirroring `can_delete_project`.

    Admin rather than owner because locking is reversible by anyone who can set it, and
    admin rather than write because the whole value of the lock is that the people who
    ordinarily edit cannot lift it themselves.

    Note an experiment with no project can never be locked: `effective_role` answers None for
    a null project before it reaches its superuser branch, so nobody holds admin on one.
    """
    if experiment is None:
        return False
    return can_admin_project(user, experiment.project)


def can_delete_experiment(user, experiment):
    """An experiment is deletable by whoever may edit the project holding it.

    Now via `can_edit_experiment`, so a locked experiment cannot be deleted either -- which
    also means it cannot be purged, since `purge_deleted` only ever sees soft-deleted rows.
    """
    return can_edit_experiment(user, experiment)


def can_add_experiment_filter(user, experiment):
    """Curating -- editing what an experiment's pages show to everyone -- is a write.

    It used to be granted by the plain view permission, which let a read-only visitor rewrite
    shared state. (`can_curate` sat beside this for the tag endpoints, with a special case for
    a `Mutation` with no experiment; tagging is gone and so is it.)

    Delegates to `can_edit_experiment`, so it refuses a locked experiment too. That one line
    is what carries the lock into the mutation editor's four write endpoints and the filter
    page, and into the controls those pages render -- every one of them already asks this
    question.
    """
    return can_edit_experiment(user, experiment)


def accessible_projects(user, minimum=ROLE_READ):
    """Every live project on which `user` holds at least `minimum`. Always a QuerySet.

    This replaces a loop over every project in the database with a per-row permission check.
    Two things in the query are easy to get subtly wrong:

    - `role__in` and the subject lookup must sit inside **one** `Q()`. Split across two
      `.filter()` calls they become two joins, asking "has some row with this role AND some
      row for this user", which is a different and wrong question.
    - `.distinct()` is required: the group join fans out one row per matching membership.
    """
    base = live(Project.objects.all())

    if user is None or not getattr(user, "is_authenticated", False):
        # Anonymous access is exactly the public projects, and only for reading.
        return base.filter(is_public=True) if minimum == ROLE_READ else Project.objects.none()

    if user.is_superuser:
        return base

    roles = roles_at_least(minimum)
    query = (Q(access_entries__role__in=roles, access_entries__user=user)
             | Q(access_entries__role__in=roles, access_entries__group__memberships__user=user)
             | Q(user=user))
    if minimum == ROLE_READ:
        query |= Q(is_public=True)
    return base.filter(query).distinct()


def project_owners(project):
    """Every user holding `owner`, via the mirror row or an additional grant."""
    return ProjectAccess.objects.filter(project=project, role=ROLE_OWNER,
                                        user__isnull=False).select_related("user")


# --- resolving what someone typed ---------------------------------------------------------


def resolve_username(name):
    """A username typed into a text box -> a User, or an AccessError saying why not.

    Exact first, then case-insensitive. Django usernames are unique case-*sensitively*, so a
    bare `iexact` can genuinely match two accounts; that is refused by name rather than
    resolved arbitrarily.
    """
    from django.contrib.auth.models import User

    name = (name or "").strip()
    if not name:
        raise AccessError("Enter a username.")

    exact = User.objects.filter(username=name).first()
    if exact is not None:
        return exact

    matches = list(User.objects.filter(username__iexact=name)[:2])
    if not matches:
        raise AccessError('There is no user named "%s".' % name)
    if len(matches) > 1:
        raise AccessError('More than one user matches "%s"; usernames are case-sensitive, '
                          'so type it exactly.' % name)
    return matches[0]


def resolve_group_name(name, user):
    """A group name typed into a text box -> an UserGroup the actor can actually see.

    Resolved against the groups `user` belongs to, never against all of them. A box that
    resolved any name would be a group-name oracle: type names until one is accepted and you
    have enumerated every group on the installation. A group you cannot see gives the same
    answer as a group that does not exist, which is the point.
    """
    from mutint_experiment.group_permissions import visible_groups

    name = (name or "").strip()
    if not name:
        raise AccessError("Enter a group name.")

    group = visible_groups(user).filter(name__iexact=name).first()
    if group is None:
        raise AccessError('There is no group named "%s" that you belong to.' % name)
    return group


# --- writing -------------------------------------------------------------------------------


def _remaining_owner_count(project, excluding_pk=None, excluding_user_id=None):
    """How many owners the project would still have.

    **`Project.user` counts, with or without a mirror row.** `effective_role` grants owner from
    that field alone, so a guard reading `ProjectAccess` by itself would happily demote away the
    last owner of a project whose owner is named only there -- which is exactly what
    `Project.objects.create(user=...)` produces, and what `test_project_user_is_owner_without_a_
    grant_row` deliberately blesses. The result would be a project with an effective owner the
    sharing page cannot show and nothing can take away.

    Counted as a set of user ids, so somebody holding both the field and a row is one owner and
    not two. `excluding_user_id` drops the subject being demoted or removed, whichever way they
    hold it.
    """
    owners = ProjectAccess.objects.filter(project=project, role=ROLE_OWNER,
                                          user__isnull=False)
    if excluding_pk is not None:
        owners = owners.exclude(pk=excluding_pk)
    owner_ids = set(owners.values_list("user_id", flat=True))
    if project.user_id is not None:
        owner_ids.add(project.user_id)
    owner_ids.discard(excluding_user_id)
    return len(owner_ids)


def _successor_owner(project, excluding_pk=None):
    """The longest-standing remaining owner, by grant age. Lowest pk is oldest."""
    entries = ProjectAccess.objects.filter(project=project, role=ROLE_OWNER,
                                           user__isnull=False)
    if excluding_pk is not None:
        entries = entries.exclude(pk=excluding_pk)
    return entries.order_by("pk").first()


def grant_project_access(project, subject, role, granted_by=None):
    """Give `subject` (a User or an UserGroup) `role` on `project`. Upserts.

    The guardrails live here rather than only in the view, so the management command and the
    import paths cannot route around them.
    """
    if not is_role(role):
        raise AccessError("Unknown role.")

    if isinstance(subject, UserGroup):
        if role == ROLE_OWNER:
            raise AccessError("A group cannot own a project; ownership is held by a person.")
        lookup = {"project": project, "group": subject}
    else:
        lookup = {"project": project, "user": subject}

    existing = ProjectAccess.objects.filter(**lookup).first()

    # Whether this grant takes ownership away from someone who currently holds it -- through
    # their row, or through `Project.user`, which confers owner on its own. Asked before the
    # write, because the write is what makes the answer stop being true.
    was_primary = (not isinstance(subject, UserGroup)
                   and project.user_id is not None
                   and project.user_id == getattr(subject, "id", None))
    holds_ownership = was_primary or (existing is not None and existing.role == ROLE_OWNER)

    if (holds_ownership and role != ROLE_OWNER
            and _remaining_owner_count(project,
                                       excluding_pk=existing.pk if existing else None,
                                       excluding_user_id=getattr(subject, "id", None)) == 0):
        raise AccessError("A project must have an owner; give ownership to someone else first.")

    if existing is not None:
        existing.role = role
        existing.granted_by = granted_by if _is_real_user(granted_by) else None
        existing.save(update_fields=["role", "granted_by"])
        entry = existing
    else:
        entry = ProjectAccess.objects.create(
            role=role, granted_by=granted_by if _is_real_user(granted_by) else None, **lookup)

    if role == ROLE_OWNER and project.user_id is None:
        project.user = subject
        project.save(update_fields=["user"])
    elif was_primary and role != ROLE_OWNER:
        # Demoting the primary owner. `Project.user` has to move with the role, or the
        # demotion does nothing at all: `effective_role` reads that field as owner without
        # consulting the row, so the sharing page would show the new lesser role beside
        # somebody the server still treats as an owner. Same successor rule as
        # `revoke_project_access` -- the guard above has already established there is one.
        successor = _successor_owner(project, excluding_pk=entry.pk)
        if successor is not None:
            project.user = successor.user
            project.save(update_fields=["user"])

    _bump_cache()
    return entry


def self_revoke_refusal(user, entry):
    """Why `user` may not remove **their own** grant, or "" if they may.

    An owner leaving a project is a transfer, not a removal: give ownership to someone else
    and then lower your own role, which `grant_project_access` allows the moment a second
    owner exists. Removing the row instead is the one shape of that move with no intermediate
    state where the project still has the owner it is about to lose -- and, on a project with
    two owners, the shape that silently makes it somebody else's without saying so.

    Deliberately not folded into `revoke_project_access`, which takes no actor: another owner
    removing an owner is ordinary administration, and `./mutint project_access` is the escape
    hatch for a project whose owner is gone. It is the *self* case that is refused, so the
    check needs to know who is asking and therefore lives beside the view that does.
    """
    if entry.user_id is None or entry.user_id != getattr(user, "id", None):
        return ""
    if entry.role == ROLE_OWNER:
        return ("You are an owner of this project and cannot remove your own access. "
                "Give ownership to someone else, then lower your own role.")
    return ""


def revoke_project_access(project, entry):
    """Remove one grant, keeping the "a project always has an owner" invariant."""
    was_primary = entry.user_id is not None and project.user_id == entry.user_id

    # `excluding_user_id` because the widened count credits `Project.user` with ownership: the
    # person whose row is being deleted must not be counted as a remaining owner through the
    # very field this removal is about to move.
    if ((entry.role == ROLE_OWNER or was_primary)
            and _remaining_owner_count(project, excluding_pk=entry.pk,
                                       excluding_user_id=entry.user_id) == 0):
        raise AccessError("A project must have an owner; give ownership to someone else first.")

    entry.delete()

    if was_primary:
        # `Project.user` is the primary owner and is displayed as *the* owner, so it cannot be
        # left pointing at someone who no longer has any access. The lowest-pk remaining owner
        # is the longest-standing one.
        successor = _successor_owner(project)
        if successor is not None:
            project.user = successor.user
            project.save(update_fields=["user"])

    _bump_cache()


def set_primary_owner(project, user, granted_by=None):
    """Make `user` the project's primary owner: writes `Project.user` **and** the owner row.

    The one writer of `Project.user`. Everything that creates a project goes through here --
    the create page, the CLI importer, `load_projects`, `load_example` -- so a project cannot
    come into existence with an owner who has no grant, which is what the old
    `Project.objects.create()` trap was.
    """
    if project.user_id != getattr(user, "id", None):
        project.user = user
        project.save(update_fields=["user"])
    entry, created = ProjectAccess.objects.get_or_create(
        project=project, user=user, group=None,
        defaults={"role": ROLE_OWNER,
                  "granted_by": granted_by if _is_real_user(granted_by) else None})
    if not created and entry.role != ROLE_OWNER:
        entry.role = ROLE_OWNER
        entry.save(update_fields=["role"])
    _bump_cache()
    return entry


def _is_real_user(user):
    return bool(user and getattr(user, "is_authenticated", False) and user.pk)

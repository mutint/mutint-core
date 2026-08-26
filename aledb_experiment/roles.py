"""The four project roles, and the ordering between them.

This module imports nothing from Django on purpose. `models.py` needs ROLE_CHOICES for a
field definition and `permissions.py` needs the ordering, so anything Django-shaped in here
would make those two import each other through it.

Roles are stored as strings rather than integers so the column is readable in /admin/ and in
a sqlite dump. The ordering lives here, in code, where it can change with the code -- and
`roles_at_least()` is what lets a "role >= write" question become a single `role__in=[...]`
lookup, which is what keeps `accessible_projects()` to one query.
"""

ROLE_READ = "read"
ROLE_WRITE = "write"
ROLE_ADMIN = "admin"
ROLE_OWNER = "owner"

ROLE_CHOICES = (
    (ROLE_READ, "Read"),
    (ROLE_WRITE, "Read/write"),
    (ROLE_ADMIN, "Admin"),
    (ROLE_OWNER, "Owner"),
)

#: Higher is more. Not stored anywhere -- see the module docstring.
ROLE_RANK = {
    ROLE_READ: 1,
    ROLE_WRITE: 2,
    ROLE_ADMIN: 3,
    ROLE_OWNER: 4,
}

#: Highest first, which is the order the sharing page lists grants in.
ROLES_BY_RANK = (ROLE_OWNER, ROLE_ADMIN, ROLE_WRITE, ROLE_READ)


def rank(role):
    """How much a role is worth. `None` and anything unrecognised are worth nothing.

    Returning 0 rather than raising is deliberate: `effective_role` answers `None` for a user
    with no access at all, and every caller then compares that answer against a minimum. A
    role read back from a row written by an older version should refuse access, not 500.
    """
    return ROLE_RANK.get(role, 0)


def at_least(role, minimum):
    """Does `role` satisfy a requirement of `minimum`?"""
    return rank(role) >= rank(minimum) > 0


def best_role(first, second):
    """The higher-ranked of two roles. None-safe, so it folds over a list of grants."""
    return first if rank(first) >= rank(second) else second


def roles_at_least(minimum):
    """Every role that satisfies `minimum`, for use as a `role__in=` lookup."""
    return [role for role in ROLES_BY_RANK if rank(role) >= rank(minimum)]


def is_role(value):
    return value in ROLE_RANK

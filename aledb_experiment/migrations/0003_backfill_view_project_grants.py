"""Inert. It once backfilled django-guardian grants; guardian is gone.

What it did: `can_view_project` read the guardian `view_project` grant and never
`Project.user`, so projects made by `load_projects` -- and any made before the two paths that
issued a grant existed -- had an owner who could not see them. This migration issued the
missing grants.

Why it is empty now: `0005` converts those grants into `ProjectAccess` rows and derives an
owner row from `Project.user` regardless, and `effective_role` treats `Project.user` as owner
directly, so the hole this filled cannot reopen. Re-running the backfill on a fresh database
would write rows into a table that no longer exists.

**The file is kept, and must not be renamed.** `aledb_seq.0005`, `aledb_stats.0003`,
`aledb_filter.0002` and `aledb_common.0001` all name it as a dependency. Its own dependencies
on `guardian` and `contenttypes` are what had to go: a dependency on an app that is no longer
in INSTALLED_APPS makes `migrate` fail with NodeNotFoundError on a *fresh* database, before
reaching any later migration -- and every test run builds a fresh database.
"""

from django.db import migrations


def noop(apps, schema_editor):
    """See the module docstring. Kept as a named function so the operation still reads."""


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_experiment", "0002_aleexperiment_deleted_at_aleexperiment_deleted_by_and_more"),
    ]

    operations = [
        migrations.RunPython(noop, noop),
    ]

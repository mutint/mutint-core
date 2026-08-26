"""Turn django-guardian's `view_project` grants, and every project's named owner, into rows.

Two sources, because the old scheme had two and they disagreed:

1. `Project.user` -- who the project said it belonged to. It was never consulted by
   `can_view_project`, which is why `Project.objects.create()` left an owner unable to see
   what they owned. Becomes an `owner` row.
2. guardian's `view_project` grants -- what `can_view_project` actually read. Becomes a
   `read` row. The owner rows from step 1 win where the two overlap.

**Guardian's table is read with raw SQL, not `apps.get_model("guardian", ...)`.** The ORM
version would only work while guardian was still in INSTALLED_APPS, which forces a
two-release dance: one release to migrate, a later one to uninstall. Reading the table
directly severs that ordering entirely -- this works in either order, in one release, and
degrades to a clean no-op once the table is gone. The introspection check is what makes it
safe on a fresh database, where the table never existed.

Note the content-type filter is `app_label = 'aledb_experiment'`. The app label is *not*
`'ale'`; every historical lookup in the old `permissions.py` filtered on `'ale'` and so
matched nothing, which is the bug that made the whole scheme look like it worked. Writing
`'ale'` here would silently convert nothing.

**Access shrinks for some users, and that is intended.** `can_view_project` used to end
`return bool(user.is_staff)`, a blanket read grant over every project, and `load_projects`
creates every imported user with `is_staff=True`. That clause is gone. Anyone who was granted
access properly keeps it -- that is what step 2 converts -- but a deployment that leaned on
the staff clause will see people lose projects they were never actually granted. Restore
specific access with `./aledb project_access grant --project <id> --user <name> --role read`.
"""

from django.db import migrations

GUARDIAN_TABLE = "guardian_userobjectpermission"


def forwards(apps, schema_editor):
    Project = apps.get_model("aledb_experiment", "Project")
    ProjectAccess = apps.get_model("aledb_experiment", "ProjectAccess")

    # Soft-deleted projects included on purpose: a project that is restored has to come back
    # with its access intact, and `deleted_at` is cleared by `restore()` alone.
    for project in Project.objects.exclude(user__isnull=True).iterator():
        ProjectAccess.objects.get_or_create(
            project_id=project.pk, user_id=project.user_id, group=None,
            defaults={"role": "owner"})

    connection = schema_editor.connection
    if GUARDIAN_TABLE not in connection.introspection.table_names():
        return

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT g.user_id, g.object_pk
              FROM %s g
              JOIN auth_permission p     ON p.id = g.permission_id
              JOIN django_content_type c ON c.id = p.content_type_id
             WHERE c.app_label = 'aledb_experiment'
               AND c.model     = 'project'
               AND p.codename  = 'view_project'
        """ % GUARDIAN_TABLE)
        grants = cursor.fetchall()

    known = set(Project.objects.values_list("pk", flat=True))
    for user_id, object_pk in grants:
        try:
            project_id = int(object_pk)   # guardian stores the pk as text
        except (TypeError, ValueError):
            continue
        if project_id not in known:
            continue                      # the grant outlived its project
        ProjectAccess.objects.get_or_create(
            project_id=project_id, user_id=user_id, group=None,
            defaults={"role": "read"})


def backwards(apps, schema_editor):
    """Reversible in schema only. The guardian rows this read are not written back."""
    ProjectAccess = apps.get_model("aledb_experiment", "ProjectAccess")
    ProjectAccess.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_experiment", "0004_alegroup_projectaccess_alegroupmembership_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

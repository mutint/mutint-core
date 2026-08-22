"""Give every existing project's owner the guardian grant it was never issued.

`can_view_project` reads the django-guardian grant, never `Project.user`. Only two code
paths ever issued one -- `/ale/projects/create/` and the CLI's `try_creating_project` -- so
projects made by `load_projects` (and any created before those paths existed) have an owner
who cannot see them.

That went unnoticed because the queries behind `get_user_projects` filtered on
`content_type__app_label='ale'` while the real label is `aledb_experiment`, so they always
returned empty and the code fell through to showing every project to everyone. Correcting
that filter is what makes the missing grants matter, so this backfill has to land with it.
"""

from django.db import migrations


def grant_owners_view_access(apps, schema_editor):
    # assign_perm needs the real model: it resolves the ContentType from the instance, and
    # historical models carry no _meta the guardian shortcuts can use.
    from django.contrib.contenttypes.models import ContentType
    from guardian.models import UserObjectPermission
    from django.contrib.auth.models import Permission

    Project = apps.get_model("aledb_experiment", "Project")
    content_type = ContentType.objects.filter(
        app_label="aledb_experiment", model="project").first()
    if content_type is None:
        return

    permission = Permission.objects.filter(
        content_type=content_type, codename="view_project").first()
    if permission is None:
        return

    for project in Project.objects.exclude(user__isnull=True).iterator():
        UserObjectPermission.objects.get_or_create(
            permission=permission,
            content_type=content_type,
            object_pk=str(project.pk),
            user_id=project.user_id)


def drop_backfilled_grants(apps, schema_editor):
    """No-op: the grants are indistinguishable from ones issued normally."""


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_experiment", "0002_aleexperiment_deleted_at_aleexperiment_deleted_by_and_more"),
        ("guardian", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(grant_owners_view_access, drop_backfilled_grants),
    ]

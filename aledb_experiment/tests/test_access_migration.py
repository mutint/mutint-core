"""The guardian -> ProjectAccess data migration.

`forwards` is called directly rather than through a migration-replay harness: there is none
in this repo, and everything interesting lives inside the function rather than in the graph
around it. `apps` is the real app registry, which is what the historical one resolves to for
these models anyway -- no field has changed since.

The guardian half **builds `guardian_userobjectpermission` itself**, with the four columns
the migration selects, and lets the test transaction roll it away. django-guardian is
uninstalled, so a test that skipped when the table was absent would skip forever and the
conversion -- the only reason this migration exists, and the thing that will run exactly once
on a real database -- would never be exercised again. Creating the table is what keeps it
tested; the migration reads it with raw SQL precisely so that neither it nor this depends on
the package being installed.
"""

import importlib

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase

from aledb_experiment.models import Project, ProjectAccess

migration = importlib.import_module(
    "aledb_experiment.migrations.0005_convert_guardian_grants_to_project_access")


class FakeSchemaEditor:
    connection = connection


def run_forwards():
    from django.apps import apps
    migration.forwards(apps, FakeSchemaEditor())


def make_user(username):
    return User.objects.create(username=username, email="%s@e.com" % username,
                               is_active=True)


class OwnerConversionTestCase(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        ProjectAccess.objects.all().delete()

    def test_a_named_owner_becomes_an_owner_row(self):
        project = Project.objects.create(name="P", user=self.owner)
        ProjectAccess.objects.all().delete()
        run_forwards()
        entry = ProjectAccess.objects.get(project=project, user=self.owner)
        self.assertEqual(entry.role, "owner")

    def test_a_soft_deleted_project_keeps_its_access(self):
        """A restored project has to come back usable; `restore()` clears only the flag."""
        project = Project.objects.create(name="P", user=self.owner)
        project.soft_delete(self.owner)
        ProjectAccess.objects.all().delete()
        run_forwards()
        self.assertTrue(ProjectAccess.objects.filter(project=project,
                                                     role="owner").exists())

    def test_running_it_twice_writes_nothing_new(self):
        Project.objects.create(name="P", user=self.owner)
        ProjectAccess.objects.all().delete()
        run_forwards()
        before = ProjectAccess.objects.count()
        run_forwards()
        self.assertEqual(ProjectAccess.objects.count(), before)


class GuardianConversionTestCase(TestCase):
    """The half that reads guardian's table directly."""

    #: The columns `forwards` selects. Guardian's real table has more; they are irrelevant
    #: here, and naming only what is read keeps this from being a schema copy that rots.
    CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS guardian_userobjectpermission (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_pk VARCHAR(255) NOT NULL,
            content_type_id INTEGER NOT NULL,
            permission_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL
        )
    """

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute(self.CREATE_TABLE)
        # SQLite rolls DDL back with the rest of the test transaction, but say so explicitly
        # so the table cannot leak into another test on a backend that does not.
        self.addCleanup(self._drop_table)
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        ProjectAccess.objects.all().delete()

    def _drop_table(self):
        try:
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS guardian_userobjectpermission")
        except Exception:   # already gone with the transaction
            pass

    def _permission_id(self):
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        content_type = ContentType.objects.get_for_model(Project)
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type, codename="view_project",
            defaults={"name": "Can view project"})
        return permission.id, content_type.id

    def _grant(self, user, project_pk):
        permission_id, content_type_id = self._permission_id()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO guardian_userobjectpermission "
                "(object_pk, content_type_id, permission_id, user_id) VALUES (%s,%s,%s,%s)",
                [str(project_pk), content_type_id, permission_id, user.id])

    def test_a_view_grant_becomes_a_read_row(self):
        project = Project.objects.create(name="P", user=self.owner)
        self._grant(self.reader, project.pk)
        ProjectAccess.objects.all().delete()
        run_forwards()
        self.assertEqual(
            ProjectAccess.objects.get(project=project, user=self.reader).role, "read")

    def test_the_owner_row_wins_over_a_view_grant_for_the_same_person(self):
        project = Project.objects.create(name="P", user=self.owner)
        self._grant(self.owner, project.pk)
        ProjectAccess.objects.all().delete()
        run_forwards()
        rows = ProjectAccess.objects.filter(project=project, user=self.owner)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().role, "owner")

    def test_a_grant_naming_a_project_that_is_gone_is_skipped(self):
        project = Project.objects.create(name="P", user=self.owner)
        stale_pk = project.pk
        self._grant(self.reader, stale_pk)
        project.delete()
        ProjectAccess.objects.all().delete()
        run_forwards()   # must not raise
        self.assertFalse(ProjectAccess.objects.filter(user=self.reader).exists())

    def test_a_non_numeric_object_pk_is_skipped(self):
        """guardian stores object_pk as text, so it can hold anything."""
        Project.objects.create(name="P", user=self.owner)
        permission_id, content_type_id = self._permission_id()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO guardian_userobjectpermission "
                "(object_pk, content_type_id, permission_id, user_id) VALUES (%s,%s,%s,%s)",
                ["not-a-number", content_type_id, permission_id, self.reader.id])
        ProjectAccess.objects.all().delete()
        run_forwards()   # must not raise
        self.assertFalse(ProjectAccess.objects.filter(user=self.reader).exists())

    def test_a_grant_on_a_different_model_is_ignored(self):
        """The content-type filter. Filtering on the stale 'ale' label matched nothing."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        project = Project.objects.create(name="P", user=self.owner)
        other_type = ContentType.objects.get_for_model(User)
        permission, _ = Permission.objects.get_or_create(
            content_type=other_type, codename="view_project",
            defaults={"name": "Can view project"})
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO guardian_userobjectpermission "
                "(object_pk, content_type_id, permission_id, user_id) VALUES (%s,%s,%s,%s)",
                [str(project.pk), other_type.id, permission.id, self.reader.id])
        ProjectAccess.objects.all().delete()
        run_forwards()
        self.assertFalse(ProjectAccess.objects.filter(user=self.reader).exists())


class NoGuardianTableTestCase(TestCase):
    """The fresh-database path: no guardian table, so the second half is a clean no-op."""

    def test_it_is_a_no_op_when_the_table_is_absent(self):
        class BareConnection:
            class introspection:
                @staticmethod
                def table_names():
                    return ["aledb_experiment_project"]

        class NoTables:
            connection = BareConnection()

        from django.apps import apps
        owner = make_user("owner")
        project = Project.objects.create(name="P", user=owner)
        ProjectAccess.objects.all().delete()
        migration.forwards(apps, NoTables())
        # The owner half still runs; only the guardian read is skipped.
        self.assertTrue(ProjectAccess.objects.filter(project=project,
                                                     role="owner").exists())

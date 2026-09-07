"""Refusing to migrate a database this version cannot migrate.

The state being guarded against -- `django_migrations` naming files that no longer exist --
is what a rewritten migration history leaves behind, and it is the reason
`docs/contributing/releasing.md` says a migration is immutable once it ships in a tag. Before
the first tag that rule cost nothing and the history was collapsed twice; MutInt upgrades in
place now, so somebody's database really does hold those rows.

The guard is not tested by rewriting migrations, which nothing can do to a running suite.
`MigrationLoader`'s two sets are what the answer is computed from, so the tests supply those.
"""

from unittest import mock

from django.db import connection
from django.test import TestCase

from mutint_common import migration_guard


def _loader(applied, on_disk):
    """A stand-in MigrationLoader carrying just the two sets the guard reads."""
    return mock.Mock(applied_migrations=applied, disk_migrations=on_disk)


class MissingMigrationsTestCase(TestCase):

    def test_a_real_database_is_consistent_with_this_checkout(self):
        """The whole point, asserted against the suite's own database rather than a fixture:
        if these ever disagree, every launch is about to refuse."""
        self.assertEqual([], migration_guard.missing_migrations(connection))

    def test_an_applied_migration_with_no_file_is_reported(self):
        loader = _loader({("mutint_sample", "0002_gone"): None},
                         {("mutint_sample", "0001_initial"): None})
        with mock.patch("django.db.migrations.loader.MigrationLoader", return_value=loader):
            self.assertEqual([("mutint_sample", "0002_gone")],
                             migration_guard.missing_migrations(connection))

    def test_a_migration_on_disk_but_not_applied_is_not_a_problem(self):
        """That is an ordinary pending migration, which is what `migrate` is for."""
        loader = _loader({}, {("mutint_sample", "0001_initial"): None})
        with mock.patch("django.db.migrations.loader.MigrationLoader", return_value=loader):
            self.assertEqual([], migration_guard.missing_migrations(connection))

    def test_third_party_apps_are_not_our_business(self):
        """A package renumbers its own history between releases and its upgrade path is
        pip's. Naming one here would alarm an operator about something they did not do and
        cannot act on."""
        loader = _loader({("django_tasks_db", "0021_gone"): None}, {})
        with mock.patch("django.db.migrations.loader.MigrationLoader", return_value=loader):
            self.assertEqual([], migration_guard.missing_migrations(connection))


class DescribeTestCase(TestCase):

    def test_nothing_missing_is_no_message(self):
        self.assertIsNone(migration_guard.describe([]))

    def test_it_names_the_migrations_and_the_ways_out(self):
        message = migration_guard.describe([("mutint_sample", "0002_gone")])

        self.assertIn("mutint_sample.0002_gone", message)
        self.assertIn("nothing has been changed", message)
        self.assertIn("upgrade --to", message)
        self.assertIn("data/backups/", message)

    def test_a_long_list_is_summarised(self):
        missing = [("mutint_sample", "%04d_x" % n) for n in range(20)]

        message = migration_guard.describe(missing)

        self.assertIn("and 14 more", message)

    def test_the_reset_advice_mentions_the_store(self):
        """`db reset` prints that warning only on its confirmation path, never under --yes,
        and store paths are derived from primary keys that restart at 1."""
        message = migration_guard.describe([("mutint_sample", "0002_gone")])

        self.assertIn("data/store", message)


class CheckTestCase(TestCase):

    def test_it_answers_none_when_the_history_is_intact(self):
        self.assertIsNone(migration_guard.check(connection))

    def test_it_cannot_raise(self):
        """It runs on the launch path. A guard that can itself fail is a way of not starting
        that nobody asked for; saying nothing leaves `migrate` as it was before this existed.
        """
        with mock.patch.object(migration_guard, "missing_migrations",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(migration_guard.check(connection))

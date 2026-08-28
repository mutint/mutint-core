"""The site-wide filter is gone, and what it hid was folded into the experiments first.

`GlobalFilter` was a second ignored-gene list -- one row for the whole installation, editable
only by a superuser, and reachable only by typing its URL, since the sole reference to its page
anywhere was a commented-out sidebar entry. `AleExperimentFilter.ignored_genes` has identical
semantics, so what went is the ability to hide a gene everywhere at once.

Dropping it without the fold would have silently un-hidden those genes in every experiment, on
any deployment that had used one. That is what `0005` exists for, and what most of this file
tests.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from aledb_experiment.models import AleExperiment, Instrument
from aledb_filter.models import AleExperimentFilter

APP = "aledb_filter"
BEFORE = "0004_drop_gatk_cutoffs"
AFTER = "0005_drop_global_filter"


class FoldForwardTestCase(TransactionTestCase):
    """Run through the real migration executor.

    `GlobalFilter` no longer exists as a model, so the only way to have a row is to stand the
    database up at 0004. Everything else is built with the ORM *before* migrating down --
    neither `AleExperiment` nor `AleExperimentFilter` is touched by 0005, so their rows survive
    the round trip and there is no need to hand-write their NOT NULL columns.
    """

    available_apps = None

    def setUp(self):
        self.one = self._experiment_filter("")
        self.two = self._experiment_filter("thrA")
        self.addCleanup(self._migrate, AFTER)
        self._migrate(BEFORE)

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(APP, target)])

    def _experiment_filter(self, genes):
        experiment = AleExperiment.objects.create(
            instrument=Instrument.objects.create())
        AleExperimentFilter.objects.create(ale_experiment=experiment, ignored_genes=genes)
        return experiment.ale_id

    def _global(self, genes):
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO aledb_filter_globalfilter (id, ignored_genes) "
                           "VALUES (1, %s)", [genes])

    def _genes_of(self, experiment_id):
        return AleExperimentFilter.objects.get(
            ale_experiment_id=experiment_id).ignored_genes

    def test_a_global_gene_lands_in_every_experiment(self):
        """The whole point. Without this the gene silently becomes visible again."""
        self._global("rrlA")

        self._migrate(AFTER)

        self.assertIn("rrlA", self._genes_of(self.one))
        self.assertIn("rrlA", self._genes_of(self.two))
        self.assertIn("thrA", self._genes_of(self.two), "what was already there survives")

    def test_a_gene_already_listed_is_not_duplicated(self):
        self._global("thrA")

        self._migrate(AFTER)

        self.assertEqual(["thrA"],
                         [g.strip() for g in self._genes_of(self.two).split(",")])

    def test_an_empty_global_filter_changes_nothing(self):
        """Which is every deployment we know of, so this is the path that actually runs."""
        self._global("")

        self._migrate(AFTER)

        self.assertEqual("thrA", self._genes_of(self.two))
        self.assertEqual("", self._genes_of(self.one))

    def test_the_table_is_gone_afterwards(self):
        self._migrate(AFTER)

        tables = connection.introspection.table_names()
        self.assertNotIn("aledb_filter_globalfilter", tables)
        self.assertIn("aledb_filter_aleexperimentfilter", tables)


class NothingReferencesItTestCase(TestCase):

    def test_the_page_is_gone(self):
        from django.contrib.auth.models import User

        admin = User.objects.create(username="admin", email="a@e.com",
                                    is_active=True, is_superuser=True)
        self.client.force_login(admin)

        self.assertEqual(404, self.client.get("/filter/global_filter").status_code)

    def test_the_filter_no_longer_takes_a_skip_global_argument(self):
        """A caller passing it would have been silently ignoring a filter it thought it was
        skipping. TypeError is the right answer."""
        import inspect

        from aledb_filter.util import filter_observed_mutations

        parameters = inspect.signature(filter_observed_mutations).parameters
        self.assertNotIn("skip_global_filter", parameters)
        self.assertIn("skip_experiment_filter", parameters)

    def test_the_shared_table_no_longer_offers_to_show_global_filtered(self):
        """It offered to reveal what a list that no longer exists was hiding."""
        import io
        import os

        from django.conf import settings

        path = None
        for directory in settings.TEMPLATES[0]["DIRS"]:
            candidate = os.path.join(directory, "base_table_template.html")
            if os.path.isfile(candidate):
                path = candidate
        if path is None:
            from aledb_common import __file__ as common_file
            path = os.path.join(os.path.dirname(common_file), "templates",
                                "base_table_template.html")

        markup = io.open(path, encoding="utf-8").read()
        self.assertNotIn('name="show_global_filtered"', markup)
        self.assertIn('name="show_exp_filtered"', markup)


class CuratePermissionTestCase(TestCase):
    """`can_curate` replaced `can_add_global_filter(user) or can_add_experiment_filter(...)`."""

    def setUp(self):
        from django.contrib.auth.models import User

        self.superuser = User.objects.create(username="root", email="r@e.com",
                                             is_active=True, is_superuser=True)
        self.nobody = User.objects.create(username="nobody", email="n@e.com", is_active=True)

    def test_a_mutation_with_no_experiment_stays_superuser_only(self):
        """The one thing the old disjunction was really for. An unscoped mutation has no
        project to grant against, so `can_add_experiment_filter` answers False for everyone --
        and deleting the left half without noticing would have made it uncurateable."""
        from aledb_experiment.permissions import can_curate

        self.assertTrue(can_curate(self.superuser, None))
        self.assertFalse(can_curate(self.nobody, None))

    def test_a_locked_experiment_refuses_a_superuser(self):
        """What the disjunction broke: the left half was `is_superuser`, so the `or`
        short-circuited before the lock on the right was ever consulted."""
        from django.contrib.auth.models import User
        from django.utils import timezone

        from aledb_experiment.permissions import can_curate

        self.client.force_login(self.superuser)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        experiment = AleExperiment.objects.get(pk=created["experiment_id"])
        self.assertTrue(can_curate(self.superuser, experiment))

        experiment.locked_at = timezone.now()
        experiment.save()

        self.assertFalse(can_curate(self.superuser, experiment))

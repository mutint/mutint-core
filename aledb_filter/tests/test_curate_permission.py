"""`can_curate`, and that the shared filter has left no way back in.

This module was `test_global_filter_removal`, which pinned `aledb_filter.0005` folding the
installation-wide ignored-gene list into each experiment's row before dropping `GlobalFilter`.
That fold-forward's *destination* is gone too now -- `0006` drops `AleExperimentFilter` and
filtering belongs to the reader -- so the tests that stood a database up at `0004` and built
rows with the ORM cannot run and have nothing left to assert.

What survives is the part that was never about the table. `can_curate` replaced
`can_add_global_filter(user) or can_add_experiment_filter(user, experiment)`, and the disjunction
it replaced had a real bug in it: the left half was `is_superuser`, so the `or` short-circuited
before the experiment lock on the right was ever consulted.
"""

from django.test import TestCase

from aledb_experiment.models import Experiment


class NoSharedFilterRemainsTestCase(TestCase):

    def test_the_pages_are_gone(self):
        """Both of them: the installation-wide one went with `GlobalFilter`, and the
        per-experiment one went when filtering became something a reader carries."""
        from django.contrib.auth.models import User

        admin = User.objects.create(username="admin", email="a@e.com",
                                    is_active=True, is_superuser=True)
        self.client.force_login(admin)

        self.assertEqual(404, self.client.get("/filter/global_filter").status_code)
        self.assertEqual(404, self.client.get("/filter").status_code)

    def test_filtering_takes_a_view_filter_and_nothing_else(self):
        """Both `skip_*` arguments are gone. A caller passing one would be silently *not*
        skipping a filter it believed it had skipped, so `TypeError` is the right answer --
        which is also why everything after the queryset is keyword-only."""
        import inspect

        from aledb_filter.util import filter_mutation_calls

        parameters = inspect.signature(filter_mutation_calls).parameters

        self.assertNotIn("skip_global_filter", parameters)
        self.assertNotIn("skip_experiment_filter", parameters)
        self.assertNotIn("experiment_id", parameters)
        self.assertIn("view_filter", parameters)
        self.assertIs(inspect.Parameter.KEYWORD_ONLY, parameters["view_filter"].kind)

    def test_the_shared_table_offers_no_way_to_see_through_somebody_elses_filter(self):
        """`show_global_filtered` offered to reveal what an installation-wide list hid;
        `show_exp_filtered` offered the same for the per-experiment row. Neither list exists,
        and clearing your own filter is what replaced the question."""
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
        self.assertNotIn('name="show_exp_filtered"', markup)
        self.assertIn("view_filter_fields", markup)


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
        from django.utils import timezone

        from aledb_experiment.permissions import can_curate

        self.client.force_login(self.superuser)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        experiment = Experiment.objects.get(pk=created["experiment_id"])
        self.assertTrue(can_curate(self.superuser, experiment))

        experiment.locked_at = timezone.now()
        experiment.save()

        self.assertFalse(can_curate(self.superuser, experiment))

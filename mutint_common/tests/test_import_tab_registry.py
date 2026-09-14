"""The import tab registry: what a tab may be, and how it renders."""

import tempfile

from django.test import TestCase, override_settings

from mutint_common import import_tab_registry as registry


class ImportTabRegistryTestCase(TestCase):
    def setUp(self):
        self.before = list(registry._tabs)
        self.addCleanup(registry._tabs.__setitem__, slice(None), self.before)

    def test_only_the_ways_in_that_work_are_offered(self):
        """An experiment with no reference can establish one, or drop something that brings
        its own -- a results folder or an archive. The tabs that would refuse are not shown
        at all."""
        tabs = registry.get_import_tabs(7)

        self.assertEqual(["reference", "breseq_folder", "mutint_archive"],
                         [t["key"] for t in tabs])

    def test_a_type_tab_lands_on_the_import_page_by_its_key(self):
        # `breseq_folder`, because a tab is only shown where its type can run and this is the
        # one that needs no reference.
        registry.register_import_tab("t", "T", import_type="breseq_folder")
        tab = [t for t in registry.get_import_tabs(7) if t["key"] == "t"][0]
        self.assertEqual({"key": "t", "label": "T", "import_types": ["breseq_folder"],
                          "url": "/import/?experiment_id=7&tab=t"}, tab)

    def test_a_tab_whose_type_cannot_run_is_not_shown(self):
        registry.register_import_tab("t", "T", import_type="vcf")

        self.assertNotIn("t", [t["key"] for t in registry.get_import_tabs(7)])

    def test_last_puts_a_tab_after_every_other(self):
        """Including a plugin's, which INSTALLED_APPS order cannot express: plugins are
        appended after every core app."""
        registry.register_import_tab("tail", "Tail", import_type="breseq_folder", last=True)
        registry.register_import_tab("p", "P", url="/somewhere/")

        self.assertEqual("tail", [t["key"] for t in registry.get_import_tabs(7)][-1])

    def test_a_page_tab_that_needs_a_reference_waits_for_one(self):
        """A tab naming an import type is hidden by asking the registry; a tab that is a page
        of its own is opaque, so it declares."""
        registry.register_import_tab("p", "P", url="/somewhere/", requires_reference=True)

        self.assertNotIn("p", [t["key"] for t in registry.get_import_tabs(7)])

    def test_a_page_tab_only_without_a_reference_goes_when_one_arrives(self):
        """The inverse flag, for a page that exists to find a reference: offered while there
        is none, gone once there is one. Checked against a real experiment, because the
        registry reads the answer off what `import_registry` offers it."""
        from django.contrib.auth.models import User
        from mutint_experiment.models import Project
        from mutint_experiment.views import _create_experiment
        from mutint_import import reference_store
        from mutint_import.tests import breseq_fixture

        registry.register_import_tab("find", "Find", url="/find/", only_without_reference=True)
        owner = User.objects.create(username="o")
        experiment = _create_experiment(Project.objects.create(name="p", user=owner), "e", owner)

        self.assertIn("find", [t["key"] for t in registry.get_import_tabs(experiment.id)])

        sequences = [("ref", breseq_fixture.SEQUENCE_A)]
        with override_settings(MUTINT_STORE_DIR=tempfile.mkdtemp()):
            reference_store.establish_or_check(
                experiment, breseq_fixture.gff3_text(sequences), sequences)

        self.assertNotIn("find", [t["key"] for t in registry.get_import_tabs(experiment.id)])

    def test_the_two_reference_flags_are_exclusive_and_for_page_tabs(self):
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X", url="/x/", requires_reference=True,
                                         only_without_reference=True)
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X", import_type="vcf",
                                         only_without_reference=True)
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X", import_type="vcf", requires_reference=True)

    def test_a_page_tab_carries_the_experiment_and_a_dead_one_is_skipped(self):
        registry.register_import_tab("p", "P", url_name="reference_view")
        registry.register_import_tab("l", "L", url="/somewhere/")
        registry.register_import_tab("d", "D", url_name="no_such_route")
        tabs = {t["key"]: t for t in registry.get_import_tabs(7)}
        self.assertEqual("/mutations/reference?experiment_id=7", tabs["p"]["url"])
        self.assertEqual("/somewhere/?experiment_id=7", tabs["l"]["url"])
        self.assertNotIn("d", tabs)

    def test_exactly_one_target(self):
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X")
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X", import_type="vcf", url="/x/")
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X", import_type="vcf", import_types=("gd",))
        with self.assertRaises(ValueError):
            registry.register_import_tab("x", "X", import_types=())

    def test_registering_a_key_again_replaces_it(self):
        registry.register_import_tab("t", "First", url="/a/")
        registry.register_import_tab("t", "Second", url="/b/")
        matches = [t for t in registry.get_import_tabs(1) if t["key"] == "t"]
        self.assertEqual([("Second", "/b/?experiment_id=1")],
                         [(t["label"], t["url"]) for t in matches])

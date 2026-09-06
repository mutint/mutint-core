"""The import tab registry: what a tab may be, and how it renders."""

from django.test import TestCase

from mutint_common import import_tab_registry as registry


class ImportTabRegistryTestCase(TestCase):
    def setUp(self):
        self.before = list(registry._tabs)
        self.addCleanup(registry._tabs.__setitem__, slice(None), self.before)

    def test_core_registers_its_four_tabs_in_the_pages_order(self):
        tabs = registry.get_import_tabs(7)
        self.assertEqual(["reference", "genomediff", "vcf", "breseq_folder"],
                         [t["key"] for t in tabs][:4])
        self.assertEqual(["reference", "replace_annotation"], tabs[0]["import_types"],
                         "one tab, two handlers, in preference order")

    def test_a_type_tab_lands_on_the_import_page_by_its_key(self):
        registry.register_import_tab("t", "T", import_type="vcf")
        tab = [t for t in registry.get_import_tabs(7) if t["key"] == "t"][0]
        self.assertEqual({"key": "t", "label": "T", "import_types": ["vcf"],
                          "url": "/import/?experiment_id=7&tab=t"}, tab)

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

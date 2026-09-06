"""The three nav sections, and where each renders.

MAIN_SECTION above the selected experiment, EXPERIMENT_SECTION indented under it, and
END_SECTION after both -- About lives there, so it sits under the experiment's pages rather
than above them. Within a section, order is INSTALLED_APPS order and there is no other lever.
"""

from django.template import loader
from django.test import TestCase

from mutint_common.nav_registry import END_SECTION, EXPERIMENT_SECTION, MAIN_SECTION, get_nav_items


class NavSectionsTestCase(TestCase):
    def test_about_is_in_the_end_section_and_nowhere_else(self):
        self.assertEqual(["About"], [i["label"] for i in get_nav_items(END_SECTION)])
        self.assertNotIn("About", [i["label"] for i in get_nav_items(MAIN_SECTION)])

    def test_the_end_section_renders_after_the_experiment_section(self):
        source = loader.get_template("base.html").template.source
        self.assertLess(source.index("{% for item in nav_main_items %}"),
                        source.index("{% for item in nav_experiment_items %}"))
        self.assertLess(source.index("{% for item in nav_experiment_items %}"),
                        source.index("{% for item in nav_end_items %}"))
        self.assertLess(source.index("{% for item in nav_end_items %}"),
                        source.index("mutint-watermark"))

    def test_the_renamed_entries(self):
        """Reference (not Reference Sequence) and Curate (not Edit Mutations), at /curate/."""
        items = {i["label"]: i["url"] for i in get_nav_items(EXPERIMENT_SECTION)}
        self.assertEqual("/mutations/reference", items["Reference"])
        self.assertEqual("/curate/", items["Curate"])
        self.assertNotIn("Reference Sequence", items)
        self.assertNotIn("Edit Mutations", items)

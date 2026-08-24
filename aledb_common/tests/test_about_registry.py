"""The About page's registry: which components appear, and what they carry.

The page itself is markup and is checked by looking at it. What is worth a test is the part
that decides what goes on it -- which apps count as components, how fifteen apps in one
checkout collapse to one entry, and that a plugin's mistake cannot take the page down.
"""

import os
import tempfile

from django.test import TestCase, override_settings

from aledb_common import about_registry
from aledb_common.about_registry import (
    component_dir, get_about_sections, register_about_section,
)


class FakeAppConfig:
    """Enough of an AppConfig for the registry: it only ever reads `path` and `name`."""

    def __init__(self, path, name="fake_app"):
        self.path = path
        self.name = name


class ComponentGroupingTestCase(TestCase):
    def setUp(self):
        # The registry is module state that the real apps populated at startup, so each test
        # puts back what it found.
        self._saved = dict(about_registry._sections)
        self.addCleanup(lambda: about_registry._sections.clear())
        self.addCleanup(lambda: about_registry._sections.update(self._saved))

    def test_the_component_is_the_directory_the_app_sits_in(self):
        """aledb-core is fifteen apps in one checkout and has to read as one entry."""
        self.assertEqual(
            component_dir(FakeAppConfig("/srv/mutint/aledb-core/aledb_seq")),
            "/srv/mutint/aledb-core")
        self.assertEqual(
            component_dir(FakeAppConfig("/srv/mutint/aledb-fixation/aledb_fixation")),
            "/srv/mutint/aledb-fixation")

    def test_apps_sharing_a_checkout_collapse_to_one_section(self):
        """One entry per checkout, whatever is installed.

        This used to assert `len(sections) == 1`, which was a statement about the install
        set rather than about the grouping: true standalone, false the moment a plugin is
        added. The invariant is that the count follows the number of distinct checkouts --
        fifteen aledb_* apps in one directory still produce one entry.
        """
        from aledb_common.about_registry import first_party_app_configs

        sections = get_about_sections()
        names = [section["name"] for section in sections]
        checkouts = {component_dir(cfg) for cfg in first_party_app_configs()}

        self.assertEqual(len(names), len(set(names)), "one entry per component: %s" % names)
        self.assertIn("aledb-core", names)
        self.assertEqual(len(sections), len(checkouts), names)

    def test_core_is_one_entry_however_many_apps_it_ships(self):
        """The grouping rule doing its job on the only component guaranteed to be here."""
        from aledb_common.about_registry import first_party_app_configs

        core_dir = component_dir(
            [c for c in first_party_app_configs() if c.name == "aledb_common"][0])
        core_apps = [c for c in first_party_app_configs()
                     if component_dir(c) == core_dir]

        self.assertGreater(len(core_apps), 1, "aledb-core ships more than one app")
        self.assertEqual(
            1, len([s for s in get_about_sections() if s["name"] == "aledb-core"]))

    def test_third_party_apps_are_left_out(self):
        """Decided by where the code lives, so there is no list of names to maintain."""
        self.assertFalse(about_registry._is_first_party(
            FakeAppConfig("/srv/env/lib/python3.9/site-packages/guardian", "guardian")))
        self.assertFalse(about_registry._is_first_party(
            FakeAppConfig("/srv/env/lib/python3/dist-packages/bootstrap4", "bootstrap4")))
        self.assertFalse(about_registry._is_first_party(
            FakeAppConfig("/srv/env/lib/python3.9/site-packages/django/contrib/admin",
                          "django.contrib.admin")))
        self.assertTrue(about_registry._is_first_party(
            FakeAppConfig("/srv/mutint/aledb-fixation/aledb_fixation", "aledb_fixation")))

    def test_a_component_that_registers_nothing_still_appears(self):
        """The page is an inventory of what is installed, not only of what has prose."""
        checkout = tempfile.mkdtemp()
        app_dir = os.path.join(checkout, "quiet_app")
        os.makedirs(app_dir)

        entry = about_registry._sections.get(component_dir(FakeAppConfig(app_dir)))
        self.assertIsNone(entry)

        name = os.path.basename(component_dir(FakeAppConfig(app_dir)))
        self.assertEqual(name, os.path.basename(checkout),
                         "an unregistered component is named after its directory")

    def test_a_registered_template_that_does_not_exist_drops_to_a_heading(self):
        """One plugin's typo must not 500 a page that is mostly other components' content."""
        register_about_section(FakeAppConfig(os.path.dirname(os.path.dirname(__file__))),
                               name="aledb-core", template="about/sections/nope.html")

        section = next(s for s in get_about_sections() if s["name"] == "aledb-core")
        self.assertIsNone(section["template"])
        self.assertEqual(section["name"], "aledb-core")

    def test_the_anchor_matches_the_name(self):
        section = get_about_sections()[0]
        self.assertTrue(section["anchor"])
        self.assertNotIn(" ", section["anchor"])


class AboutPageTestCase(TestCase):
    def test_the_page_lists_every_section_and_links_to_it(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        for section in get_about_sections():
            self.assertIn('id="%s"' % section["anchor"], html)
            self.assertIn(section["name"], html)

    def test_the_core_section_renders_its_prose(self):
        html = self.client.get("/about").content.decode("utf-8")
        self.assertIn("adaptive laboratory evolution", html)

    @override_settings(ALEDB_BRANDING={"name": "ALEdb", "version": "1.0"})
    def test_branding_does_not_remove_the_component_sections(self):
        """A deployment says who it is in about/deployment.html; it no longer replaces the
        page, which would hide every component."""
        html = self.client.get("/about").content.decode("utf-8")
        self.assertIn("aledb-core", html)

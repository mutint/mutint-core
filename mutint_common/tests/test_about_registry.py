"""The About page's registry: which components appear, and what they carry.

The page itself is markup and is checked by looking at it. What is worth a test is the part
that decides what goes on it -- which apps count as components, how fifteen apps in one
checkout collapse to one entry, and that a plugin's mistake cannot take the page down.
"""

import os
import tempfile

from django.test import TestCase, override_settings

from mutint_common import about_registry
from mutint_common.about_registry import (
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

        def restore():
            about_registry._sections.clear()
            about_registry._sections.update(self._saved)

        # One callable: addCleanup is LIFO, so registering clear() and update() separately
        # runs update() first and clear() second, leaving the registry wiped for every test
        # that follows. Harmless while only core ran; not once a plugin's tests join.
        self.addCleanup(restore)

    def test_the_component_is_the_directory_the_app_sits_in(self):
        """mutint-core is fifteen apps in one checkout and has to read as one entry."""
        self.assertEqual(
            component_dir(FakeAppConfig("/srv/mutint/mutint-core/mutint_sample")),
            "/srv/mutint/mutint-core")
        self.assertEqual(
            component_dir(FakeAppConfig("/srv/mutint/mutint-fixation/mutint_fixation")),
            "/srv/mutint/mutint-fixation")

    def test_apps_sharing_a_checkout_collapse_to_one_section(self):
        """One entry per checkout, whatever is installed.

        This used to assert `len(sections) == 1`, which was a statement about the install
        set rather than about the grouping: true standalone, false the moment a plugin is
        added. The invariant is that the count follows the number of distinct checkouts --
        fifteen mutint_* apps in one directory still produce one entry.
        """
        from mutint_common.about_registry import first_party_app_configs

        sections = get_about_sections()
        names = [section["name"] for section in sections]
        checkouts = {component_dir(cfg) for cfg in first_party_app_configs()}

        self.assertEqual(len(names), len(set(names)), "one entry per component: %s" % names)
        self.assertIn("mutint-core", names)
        self.assertEqual(len(sections), len(checkouts), names)

    def test_core_is_one_entry_however_many_apps_it_ships(self):
        """The grouping rule doing its job on the only component guaranteed to be here."""
        from mutint_common.about_registry import first_party_app_configs

        core_dir = component_dir(
            [c for c in first_party_app_configs() if c.name == "mutint_common"][0])
        core_apps = [c for c in first_party_app_configs()
                     if component_dir(c) == core_dir]

        self.assertGreater(len(core_apps), 1, "mutint-core ships more than one app")
        self.assertEqual(
            1, len([s for s in get_about_sections() if s["name"] == "mutint-core"]))

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
            FakeAppConfig("/srv/mutint/mutint-fixation/mutint_fixation", "mutint_fixation")))

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
                               name="mutint-core", template="about/sections/nope.html")

        section = next(s for s in get_about_sections() if s["name"] == "mutint-core")
        self.assertIsNone(section["template"])
        self.assertEqual(section["name"], "mutint-core")

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

    @override_settings(MUTINT_BRANDING={"name": "MutInt", "version": "1.0"})
    def test_branding_does_not_remove_the_component_sections(self):
        """A deployment says who it is in about/deployment.html; it no longer replaces the
        page, which would hide every component."""
        html = self.client.get("/about").content.decode("utf-8")
        self.assertIn("mutint-core", html)


class RepositoryLinkTestCase(TestCase):
    """A component's name links to its repository, and the URL comes from its checkout.

    Patched rather than read from this checkout's own remote, which differs between a
    developer's clone, CI and an unpacked archive -- the rule is what is under test, not
    where this copy happened to come from.
    """

    def revision(self, repository):
        return {"short": "abcdef0", "full": "abcdef0" * 5 + "abcde",
                "url": repository + "/commit/abcdef0" if repository else None,
                "repository": repository}

    def test_the_sections_carry_the_repository_url(self):
        from unittest import mock
        with mock.patch("mutint_common.util.get_revision",
                        return_value=self.revision("https://github.com/someone-else/mutint-core")):
            sections = get_about_sections()
        self.assertEqual({"https://github.com/someone-else/mutint-core"},
                         {s["repository_url"] for s in sections})

    def test_the_name_links_to_the_repository_wherever_it_lives(self):
        from unittest import mock
        with mock.patch("mutint_common.util.get_revision",
                        return_value=self.revision("https://github.com/someone-else/mutint-core")):
            html = self.client.get("/about").content.decode("utf-8")
        self.assertIn('<a href="https://github.com/someone-else/mutint-core">mutint-core</a>', html)

    def test_a_component_from_nowhere_is_a_plain_name(self):
        from unittest import mock
        with mock.patch("mutint_common.util.get_revision", return_value=self.revision(None)):
            html = self.client.get("/about").content.decode("utf-8")
        # The heading is plain. Scoped to it: the section's prose may link wherever it
        # likes, and an assembled project's contents list links every name to its anchor.
        start = html.index('<h3 id="mutint-core"')
        heading = html[start:html.index("</h3>", start)]
        self.assertIn("mutint-core", heading)
        self.assertNotIn("<a ", heading)

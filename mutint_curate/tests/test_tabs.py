"""The Curate strip is a registry, so a plugin's page can sit in it.

Core's five tabs are registered from `mutint_curate.apps`; these check the strip they draw,
and that a tab registered after them lands where INSTALLED_APPS order says -- after Copy and
before History, which is `last`.
"""

import re

from django.test import SimpleTestCase

from mutint_common.curate_tab_registry import (
    get_curate_tabs, register_curate_tab, unregister_curate_tab,
)
from mutint_curate.tests.base import EditorTestCase


CORE = ["Edit", "Delete", "Add", "Copy", "History"]


def _labels(experiment_id=7):
    return [tab["label"] for tab in get_curate_tabs(experiment_id)]


def _in_order(labels, wanted):
    """`wanted` appear in `labels` in that relative order, whatever else is between them.

    Assembled, a plugin's tab is in this strip too (mutint-breseq's Evidence), so the tests
    assert order rather than the exact list -- the shared-registry rule in the testing notes.
    """
    positions = [labels.index(label) for label in wanted]
    return positions == sorted(positions)


class RegistryTestCase(SimpleTestCase):

    def test_core_registers_its_five_in_order_with_history_last(self):
        labels = _labels()
        self.assertTrue(_in_order(labels, CORE), labels)
        self.assertEqual("History", labels[-1])

    def test_a_registered_tab_lands_before_history(self):
        register_curate_tab("extra", "Extra", url="/extra/")
        self.addCleanup(unregister_curate_tab, "extra")
        labels = _labels()
        self.assertTrue(_in_order(labels, ["Copy", "Extra", "History"]), labels)
        self.assertEqual("History", labels[-1])

    def test_every_tab_carries_the_experiment(self):
        for tab in get_curate_tabs(42):
            self.assertTrue(tab["url"].endswith("?experiment_id=42"), tab)

    def test_a_url_name_that_will_not_reverse_is_skipped(self):
        # nav_registry's posture: a half-installed plugin cannot leave a dead tab.
        register_curate_tab("ghost", "Ghost", url_name="no_such_route_anywhere")
        self.addCleanup(unregister_curate_tab, "ghost")
        self.assertNotIn("Ghost", _labels())

    def test_a_url_name_is_reversed_at_render_time(self):
        register_curate_tab("named", "Named", url_name="mutation_history")
        self.addCleanup(unregister_curate_tab, "named")
        urls = {tab["label"]: tab["url"] for tab in get_curate_tabs(3)}
        self.assertEqual("/curate/history?experiment_id=3", urls["Named"])

    def test_registering_a_key_again_replaces_it(self):
        register_curate_tab("extra", "One", url="/one/")
        self.addCleanup(unregister_curate_tab, "extra")
        register_curate_tab("extra", "Two", url="/two/")
        self.assertEqual(1, _labels().count("Two"))
        self.assertNotIn("One", _labels())

    def test_it_needs_exactly_one_address(self):
        with self.assertRaises(ValueError):
            register_curate_tab("bad", "Bad")
        with self.assertRaises(ValueError):
            register_curate_tab("bad", "Bad", url="/x/", url_name="y")


class StripTestCase(EditorTestCase):

    def _strip(self, url="/curate/add"):
        response = self.client.get(url, {"experiment_id": self.experiment.id})
        strip = re.search(r'<ul class="nav nav-tabs"[^>]*>(.*?)</ul>',
                          response.content.decode(), re.S)
        self.assertIsNotNone(strip, "no tab strip on the page")
        return re.findall(r'<a href="([^"]+)">([^<]+)</a></li>', strip.group(1))

    def test_the_pages_draw_the_registered_strip(self):
        labels = [label for _, label in self._strip()]
        self.assertTrue(_in_order(labels, CORE), labels)
        self.assertEqual("History", labels[-1])

    def test_a_plugins_tab_is_drawn_on_core_pages(self):
        register_curate_tab("extra", "Extra", url="/extra/")
        self.addCleanup(unregister_curate_tab, "extra")
        strip = self._strip()
        self.assertIn(("/extra/?experiment_id=%d" % self.experiment.id, "Extra"), strip)
        self.assertEqual("History", strip[-1][1])

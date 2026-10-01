"""`/filter/set`: the reader's filter written back from a page that applies it in the browser.

Compare applies the filter as it is typed, and the filter is still one filter, the reader's,
read by every other page from the session -- so what this endpoint stores has to be exactly
what `get_view_filter` reads next, normalized the one way `ViewFilter.parse` normalizes, and a
value parse refuses must store nothing.
"""

import json

from django.test import RequestFactory, TestCase

from mutint_experiment.ancestor import ancestral_shown
from mutint_filter.view_filter import get_view_filter


class FilterSetTestCase(TestCase):
    URL = "/filter/set/"

    def post(self, payload):
        return self.client.post(self.URL, json.dumps(payload), content_type="application/json")

    def read_back(self, experiment_id=7):
        """What any other page would read: a fresh request on this client's session."""
        request = RequestFactory().get("/")
        request.session = self.client.session
        return get_view_filter(request, experiment_id), ancestral_shown(request, experiment_id)

    def test_it_stores_what_the_other_pages_read_and_answers_normalized(self):
        response = self.post({"experiment_id": 7, "filter": {
            "min_freq": "10", "max_freq": "100", "genes": "thrA, thrB,thrA"}})
        self.assertEqual(200, response.status_code)
        # 100 is the end of the scale and hides nothing, so it is no bound; the gene list is
        # split, de-duplicated and kept in the order typed.
        self.assertEqual({"min_freq": 10, "max_freq": None, "genes": ["thrA", "thrB"]},
                         response.json()["filter"])
        view_filter, _shown = self.read_back()
        self.assertEqual(10, view_filter.min_freq)
        self.assertEqual(("thrA", "thrB"), view_filter.genes)

    def test_a_refused_value_is_a_sentence_and_stores_nothing(self):
        self.post({"experiment_id": 7, "filter": {"min_freq": "20"}})
        response = self.post({"experiment_id": 7, "filter": {"min_freq": "60", "max_freq": "40"}})
        self.assertEqual(400, response.status_code)
        self.assertIn("above the maximum", response.json()["error"])
        self.assertEqual(20, self.read_back()[0].min_freq)

    def test_an_empty_filter_clears_it(self):
        self.post({"experiment_id": 7, "filter": {"min_freq": "20"}})
        self.post({"experiment_id": 7, "filter": {"min_freq": "", "max_freq": "", "genes": ""}})
        self.assertTrue(self.read_back()[0].is_empty)

    def test_it_flips_the_ancestral_choice_too(self):
        self.post({"experiment_id": 7, "ancestral": True})
        self.assertTrue(self.read_back()[1])
        self.assertFalse(self.read_back(8)[1], "one choice per experiment")
        self.post({"experiment_id": 7, "ancestral": False})
        self.assertFalse(self.read_back()[1])

    def test_it_wants_an_experiment_and_a_post(self):
        self.assertEqual(400, self.post({"filter": {}}).status_code)
        self.assertEqual(405, self.client.get(self.URL).status_code)

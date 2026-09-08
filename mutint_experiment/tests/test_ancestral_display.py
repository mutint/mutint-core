"""The reader's choice to see the subtracted rows, and how it is remembered.

`ancestral_shown` is display only -- nothing derived reads it -- so what these tests are about
is the memory: the parameter wins and is remembered, the session answers when there is no
parameter, hiding forgets, and one experiment's choice is not another's. The shape is
`get_view_filter`'s, and the traps are the same ones its docstring lists.
"""

from django.test import RequestFactory, SimpleTestCase

from mutint_experiment.ancestor import (ANCESTRAL_SESSION_KEY, MAX_REMEMBERED_EXPERIMENTS,
                                       ancestral_shown, ancestral_toggle_url)


class DisplayToggleTestCase(SimpleTestCase):

    def request(self, query="", session=None):
        request = RequestFactory().get("/mutations/breseq" + ("?" + query if query else ""))
        request.session = {} if session is None else session
        return request

    def test_hidden_by_default(self):
        self.assertFalse(ancestral_shown(self.request(), 4))

    def test_show_is_remembered(self):
        request = self.request("ancestral=show")
        self.assertTrue(ancestral_shown(request, 4))
        self.assertEqual({"4": True}, request.session[ANCESTRAL_SESSION_KEY])

    def test_the_session_answers_when_there_is_no_parameter(self):
        """The sidebar's links carry no parameters; the choice has to follow the reader."""
        session = {ANCESTRAL_SESSION_KEY: {"4": True}}
        self.assertTrue(ancestral_shown(self.request(session=session), 4))

    def test_hide_forgets(self):
        session = {ANCESTRAL_SESSION_KEY: {"4": True}}
        request = self.request("ancestral=hide", session=session)
        self.assertFalse(ancestral_shown(request, 4))
        self.assertEqual({}, request.session[ANCESTRAL_SESSION_KEY])

    def test_an_unknown_value_hides(self):
        self.assertFalse(ancestral_shown(self.request("ancestral=bogus"), 4))

    def test_one_experiment_does_not_answer_for_another(self):
        session = {ANCESTRAL_SESSION_KEY: {"4": True}}
        self.assertFalse(ancestral_shown(self.request(session=session), 5))

    def test_an_unreadable_stored_shape_is_discarded(self):
        """A session outlives a deploy; a stored shape this version cannot read costs the
        reader their choice, not the page."""
        session = {ANCESTRAL_SESSION_KEY: ["4"]}
        self.assertFalse(ancestral_shown(self.request(session=session), 4))
        request = self.request("ancestral=show", session={ANCESTRAL_SESSION_KEY: "junk"})
        self.assertTrue(ancestral_shown(request, 4))

    def test_the_memory_is_bounded(self):
        session = {ANCESTRAL_SESSION_KEY: {str(n): True
                                           for n in range(MAX_REMEMBERED_EXPERIMENTS)}}
        request = self.request("ancestral=show", session=session)
        ancestral_shown(request, 999)
        stored = request.session[ANCESTRAL_SESSION_KEY]
        self.assertEqual(MAX_REMEMBERED_EXPERIMENTS, len(stored))
        self.assertIn("999", stored)
        self.assertNotIn("0", stored)

    def test_the_answer_is_memoised_on_the_request(self):
        """The view and the summary tag ask the same request; a second parameter-less call
        must not read the session back and answer differently."""
        request = self.request("ancestral=show")
        self.assertTrue(ancestral_shown(request, 4))
        request.session[ANCESTRAL_SESSION_KEY] = {}
        self.assertTrue(ancestral_shown(request, 4))


class ToggleUrlTestCase(SimpleTestCase):

    def test_it_flips_the_value_and_keeps_the_rest(self):
        request = RequestFactory().get("/compare/?experiment_id=4&convergent_min=2")
        url = ancestral_toggle_url(request, shown=False)
        self.assertTrue(url.startswith("?"))
        self.assertIn("experiment_id=4", url)
        self.assertIn("convergent_min=2", url)
        self.assertIn("ancestral=show", url)
        self.assertIn("ancestral=hide", ancestral_toggle_url(request, shown=True))

    def test_it_replaces_rather_than_repeats_the_parameter(self):
        request = RequestFactory().get("/compare/?experiment_id=4&ancestral=show")
        url = ancestral_toggle_url(request, shown=True)
        self.assertEqual(1, url.count("ancestral="))
        self.assertIn("ancestral=hide", url)

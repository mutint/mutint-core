"""The filter value itself, and the session it lives in.

The value half needs no database, which is the point of it being its own module: pinning the
gene rules used to take six model rows and an experiment to hang them off.

The session half needs a request, and every case here is one of the traps written out in
`view_filter`'s module docstring -- the JSON serializer, the string keys, and the nested write
that silently does not save. Each is invisible to a casual test and each has a real symptom.
"""

from django.test import RequestFactory, SimpleTestCase, TestCase

from mutint_filter.view_filter import (
    EMPTY, MAX_REMEMBERED_EXPERIMENTS, SESSION_KEY, ViewFilter, clear_view_filter,
    get_view_filter, set_view_filter,
)


class ViewFilterValueTestCase(SimpleTestCase):

    # ---- normalization ----------------------------------------------------------------
    def test_the_ends_of_the_scale_hide_nothing(self):
        """0 and 100 normalize away, so a filter that excludes nothing *is* the empty one.

        This is what lets `is_empty` be the single question. A stored 0-100 row used to be
        `configured` but not `applied`, and every reader of that distinction had to remember
        which of the two they wanted.
        """
        self.assertTrue(ViewFilter.parse(min_freq=0, max_freq=100).is_empty)
        self.assertEqual(EMPTY, ViewFilter.parse(min_freq="0", max_freq="100"))

    def test_a_real_cutoff_is_not_empty(self):
        self.assertFalse(ViewFilter.parse(min_freq=20).is_empty)
        self.assertEqual(20, ViewFilter.parse(min_freq="20").min_freq)

    def test_a_blank_box_means_not_set(self):
        """What a cleared input actually posts."""
        self.assertTrue(ViewFilter.parse(min_freq="", max_freq="  ", genes="").is_empty)

    def test_genes_keep_the_order_they_were_typed_in(self):
        """The summary sentence reads them back, so the order is the reader's."""
        self.assertEqual(("insH", "rrlA"), ViewFilter.parse(genes="insH, rrlA").genes)

    def test_a_repeated_gene_is_listed_once(self):
        self.assertEqual(("insH",), ViewFilter.parse(genes="insH, insH").genes)

    def test_genes_parse_the_way_a_mutation_gene_column_does(self):
        """`get_gene_list` is shared with the mutation's own gene column, so what counts as a
        gene name is decided in one place -- including the brackets an intergenic call carries."""
        self.assertEqual(("thrB", "thrC"), ViewFilter.parse(genes="[thrB], thrC").genes)

    # ---- refusals ---------------------------------------------------------------------
    def test_a_non_number_is_refused(self):
        with self.assertRaises(ValueError):
            ViewFilter.parse(min_freq="abc")

    def test_an_out_of_range_percent_is_refused(self):
        with self.assertRaises(ValueError):
            ViewFilter.parse(max_freq=101)

    def test_a_floor_above_the_ceiling_is_refused(self):
        """Not merely odd: handed to `.exclude()` it hides every row in the experiment."""
        with self.assertRaises(ValueError):
            ViewFilter.parse(min_freq=80, max_freq=20)

    # ---- the session round trip -------------------------------------------------------
    def test_it_survives_the_session_round_trip(self):
        original = ViewFilter.parse(min_freq=20, max_freq=90, genes="insH, rrlA")

        self.assertEqual(original, ViewFilter.from_session_dict(original.to_session_dict()))

    def test_what_is_stored_is_json_native(self):
        """The session uses JSONSerializer: a tuple or a set would not survive, and a set
        would not even serialize."""
        stored = ViewFilter.parse(min_freq=20, genes="insH").to_session_dict()

        self.assertIsInstance(stored["genes"], list)
        for value in stored.values():
            self.assertIsInstance(value, (int, str, list, type(None)))

    def test_an_unreadable_stored_value_costs_only_the_filter(self):
        """A session outlives a deploy. A shape this version cannot read must not 500 every
        page for everyone still holding the old cookie."""
        for junk in ({"v": 999, "min": 20}, {"min": "abc"}, "not a dict", None, {}):
            with self.subTest(stored=junk):
                self.assertEqual(EMPTY, ViewFilter.from_session_dict(junk))


class ViewFilterSessionTestCase(TestCase):
    """The session half. `Client` rather than RequestFactory where a real session is needed."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, path="/", session=None):
        request = self.factory.get(path)
        request.session = {} if session is None else session
        return request

    def test_it_is_remembered_per_experiment(self):
        request = self._request()
        set_view_filter(request, 1, ViewFilter.parse(min_freq=20))
        set_view_filter(request, 2, ViewFilter.parse(min_freq=50))

        self.assertEqual(20, get_view_filter(self._request(session=request.session), 1).min_freq)
        self.assertEqual(50, get_view_filter(self._request(session=request.session), 2).min_freq)

    def test_the_stored_key_is_a_string(self):
        """JSONSerializer turns an integer key into a string on the way out, so a lookup by
        integer hits on the write request and misses on every request after -- which reads as
        'the filter does not stick' and no single-request test would see it."""
        request = self._request()
        set_view_filter(request, 7, ViewFilter.parse(min_freq=20))

        self.assertIn("7", request.session[SESSION_KEY])

    def test_an_experiment_with_no_filter_reads_as_empty(self):
        self.assertEqual(EMPTY, get_view_filter(self._request(), 1))

    def test_query_parameters_win_and_are_remembered(self):
        session = {}
        request = self._request("/?min_freq=20", session=session)

        self.assertEqual(20, get_view_filter(request, 1).min_freq)
        # And the next page, which carries no parameters, still has it.
        self.assertEqual(20, get_view_filter(self._request(session=session), 1).min_freq)

    def test_empty_parameters_clear_it(self):
        """The clear link sends the parameters present and empty. Presence is what is tested,
        not truthiness -- otherwise there would be no way to say 'no filter' in a URL."""
        session = {}
        set_view_filter(self._request(session=session), 1, ViewFilter.parse(min_freq=20))

        request = self._request("/?min_freq=&max_freq=&ignore_genes=", session=session)

        self.assertTrue(get_view_filter(request, 1).is_empty)
        self.assertNotIn("1", session.get(SESSION_KEY, {}))

    def test_an_unusable_parameter_leaves_the_page_unfiltered(self):
        """It must not raise: the reader would get a 500 for a typo in a URL."""
        request = self._request("/?min_freq=abc", session={})

        self.assertTrue(get_view_filter(request, 1).is_empty)

    def test_clearing_forgets_the_entry_rather_than_storing_an_empty_one(self):
        session = {}
        request = self._request(session=session)
        set_view_filter(request, 1, ViewFilter.parse(min_freq=20))

        clear_view_filter(request, 1)

        self.assertEqual({}, session[SESSION_KEY])

    def test_only_so_many_experiments_are_remembered(self):
        """`SESSION_SAVE_EVERY_REQUEST` is True, so this row is rewritten on every request.
        Without a cap a reader who has visited hundreds of experiments pays for all of them on
        every page load."""
        request = self._request()
        for experiment_id in range(MAX_REMEMBERED_EXPERIMENTS + 5):
            set_view_filter(request, experiment_id, ViewFilter.parse(min_freq=20))

        stored = request.session[SESSION_KEY]

        self.assertEqual(MAX_REMEMBERED_EXPERIMENTS, len(stored))
        self.assertNotIn("0", stored, "the oldest should have been evicted")
        self.assertIn(str(MAX_REMEMBERED_EXPERIMENTS + 4), stored, "the newest is kept")

    def test_re_filtering_an_experiment_keeps_it_recent(self):
        request = self._request()
        set_view_filter(request, 0, ViewFilter.parse(min_freq=20))
        for experiment_id in range(1, MAX_REMEMBERED_EXPERIMENTS):
            set_view_filter(request, experiment_id, ViewFilter.parse(min_freq=20))
        set_view_filter(request, 0, ViewFilter.parse(min_freq=50))
        set_view_filter(request, 999, ViewFilter.parse(min_freq=20))

        self.assertIn("0", request.session[SESSION_KEY], "re-filtering should refresh it")

    def test_a_real_session_actually_persists_the_write(self):
        """The trap worth a test of its own: assigning into the nested dict leaves
        `session.modified` False and the write is lost with no error. A plain dict stands in
        for the session in the tests above and would not catch it; this uses the real one."""
        from django.contrib.sessions.backends.db import SessionStore

        session = SessionStore()
        request = self._request(session=session)
        set_view_filter(request, 1, ViewFilter.parse(min_freq=20))
        session.save()

        reloaded = SessionStore(session_key=session.session_key)

        self.assertEqual(20, ViewFilter.from_session_dict(
            reloaded[SESSION_KEY]["1"]).min_freq)

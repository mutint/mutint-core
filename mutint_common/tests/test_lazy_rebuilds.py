"""Every derived table has to be rebuilt by whoever reads it.

`rebuild_registry` documents the contract -- `request_rebuild` marks, and the page that reads
the data calls `ensure_fresh` -- and for a long time exactly one reader honoured it. Six
rebuilders were registered; only `overview` refreshed itself. So an experiment filter change,
which deliberately marks and does not rebuild, left the needle plot, Fixed Mutations,
Convergence and the dashboard showing what was true under the previous cutoff, with nothing
short of `./mutint rebuild` that would ever correct them.

The sharpest case was one page: `/stats` renders `get_experiment_summary`, which refreshed, and
the needle plot's data, which did not -- two numbers derived from the same mutations,
disagreeing in the same viewport. **Neither is stored any more**, so that particular
disagreement is now impossible rather than merely tested for. The assertion that they still
agree has moved to `mutint_needle`, which is where the plot lives now and the only place both
halves can be read; what is left here is the half core owns, which is that nothing is
registered to rebuild the plot.

Each remaining test has the same shape: make the data current, change the mutations *behind*
the reader's back, mark stale the way a filter edit does, and read through the public entry
point.
"""


from mutint_common.rebuild_registry import is_stale, request_rebuild, run_rebuilds
from mutint_mutation_editor.tests.base import EditorTestCase
from mutint_sample.models import MutationCall


class LazyRebuildTestCase(EditorTestCase):

    def setUp(self):
        super().setUp()
        run_rebuilds(self.experiment.id, force=True)

    def _add_a_mutation(self):
        """A change the derived tables cannot see until they are rebuilt."""
        mutation = self.make_mutation(position=4321, sequence_change="A>C")
        self.observe(self.sample_a, mutation)
        return mutation

    # --- the needle plot ------------------------------------------------------------------

    def test_nothing_is_registered_to_rebuild_the_needle_plot(self):
        """It used to be `StaticData`, and this test used to prove the reader refreshed it.

        There is no stored copy to be stale now. What core can still assert is that nobody is
        registered to keep one -- `get_rebuilders` skips an unknown name silently, so a
        rebuilder coming back for a table that no longer exists would be quiet. That the
        plot itself reflects a change immediately is asserted in `mutint_needle`, which is the
        only place the plot can be read from.
        """
        from mutint_common.rebuild_registry import get_rebuilder

        self.assertIsNone(get_rebuilder('static_data'), "the rebuilder is gone")

    # --- the dashboard --------------------------------------------------------------------

    def test_the_dashboard_rebuilds_its_totals(self):
        response = self.client.get("/dashboard")

        self.assertEqual(200, response.status_code)
        self.assertFalse(is_stale("mutation_counts"))
        self.assertFalse(is_stale("sample_counts"))

    def test_a_mutation_edit_leaves_the_totals_for_the_dashboard(self):
        """The two halves together: the edit marks, the dashboard runs."""
        from mutint_mutation_editor import history

        self._add_a_mutation()
        history.rebuild_after_edit(self.experiment)
        self.assertTrue(is_stale("mutation_counts"), "the edit does not pay for this")

        self.client.get("/dashboard")

        self.assertFalse(is_stale("mutation_counts"), "the dashboard does")

    # --- a rebuild that fails must not take the page with it ------------------------------

    def test_a_failing_rebuild_still_renders_the_page(self):
        """`ensure_fresh` logs, records `last_error` and returns False. A plugin whose rebuild
        raises degrades its own page; it does not 500 the dashboard."""
        from mutint_common.models import DerivedDataState
        from mutint_common.rebuild_registry import (
            get_rebuilder, register_rebuilder, unregister_rebuilder,
        )

        def explode():
            raise RuntimeError("no")

        original = get_rebuilder("mutation_counts")
        unregister_rebuilder("mutation_counts")
        register_rebuilder("mutation_counts", explode, scope=original["scope"],
                           priority=original["priority"])
        self.addCleanup(lambda: (unregister_rebuilder("mutation_counts"),
                                 register_rebuilder("mutation_counts", original["fn"],
                                                    scope=original["scope"],
                                                    priority=original["priority"])))
        request_rebuild(reason='test')

        response = self.client.get("/dashboard")

        self.assertEqual(200, response.status_code)
        state = DerivedDataState.objects.get(name="mutation_counts", experiment=None)
        self.assertIn("no", state.last_error)
        self.assertIsNotNone(state.stale_since, "left stale so the next reader tries again")

"""Every derived table has to be rebuilt by whoever reads it.

`rebuild_registry` documents the contract -- `request_rebuild` marks, and the page that reads
the data calls `ensure_fresh` -- and for a long time exactly one reader honoured it. Six
rebuilders were registered; only `overview` refreshed itself. So an experiment filter change,
which deliberately marks and does not rebuild, left the needle plot, Fixed Mutations,
Convergence and the dashboard showing what was true under the previous cutoff, with nothing
short of `./aledb rebuild` that would ever correct them.

The sharpest case was one page: `/stats` renders `get_experiment_summary`, which refreshed, and
`get_needle_plot_data`, which did not -- two numbers derived from the same mutations,
disagreeing in the same viewport. **Neither is stored any more**, so that particular
disagreement is now impossible rather than merely tested for; the page-level assertion below
is kept anyway, because what it states is a property of the page rather than of a cache.

Each remaining test has the same shape: make the data current, change the mutations *behind*
the reader's back, mark stale the way a filter edit does, and read through the public entry
point.
"""

from decimal import Decimal

from aledb_common.rebuild_registry import is_stale, request_rebuild, run_rebuilds
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import ObservedMutation


class LazyRebuildTestCase(EditorTestCase):

    def setUp(self):
        super().setUp()
        run_rebuilds(self.experiment.ale_id, force=True)

    def _add_a_mutation(self):
        """A change the derived tables cannot see until they are rebuilt."""
        mutation = self.make_mutation(position=4321, sequence_change="A>C")
        self.observe(self.sample_a, mutation)
        return mutation

    # --- the needle plot ------------------------------------------------------------------

    def test_the_needle_plot_has_nothing_to_rebuild(self):
        """It used to be `StaticData`, and this test used to prove the reader refreshed it.

        There is no stored copy to be stale now, so the property worth asserting is the
        stronger one the removal bought: the change is visible without anything marking,
        rebuilding, or being asked to. `static_data` is not a registered rebuilder any more,
        and `get_rebuilders` skipping an unknown name is what makes that silent -- so this
        also pins that nobody has to remember to ask.
        """
        from aledb_common.rebuild_registry import get_rebuilder
        from aledb_stats.util import get_needle_plot_data

        self.assertIsNone(get_rebuilder('static_data'), "the rebuilder is gone")

        before = len(get_needle_plot_data(self.experiment.ale_id))
        self._add_a_mutation()

        self.assertEqual(before + 1, len(get_needle_plot_data(self.experiment.ale_id)))

    def test_the_needle_plot_and_the_overview_agree_on_one_page(self):
        """They are rendered together by `/stats`, and used to be two caches that could fall
        out of step. They are two queries over the same rows now, which is why this is left
        marked stale and not rebuilt: whatever `request_rebuild` did or did not do, both
        halves of the page have to answer for the same mutations."""
        from aledb_stats.util import get_experiment_summary, get_needle_plot_data

        self._add_a_mutation()
        request_rebuild(self.experiment.ale_id, reason='test')

        summary = get_experiment_summary(self.experiment.ale_id)
        needle = get_needle_plot_data(self.experiment.ale_id)
        self.assertEqual(sum(summary.observed_mutation_type_counts.values()), len(needle))

    # --- the dashboard --------------------------------------------------------------------

    def test_the_dashboard_rebuilds_its_totals(self):
        response = self.client.get("/dashboard")

        self.assertEqual(200, response.status_code)
        self.assertFalse(is_stale("mutation_counts"))
        self.assertFalse(is_stale("sample_counts"))

    def test_a_mutation_edit_leaves_the_totals_for_the_dashboard(self):
        """The two halves together: the edit marks, the dashboard runs."""
        from aledb_mutation_editor import history

        self._add_a_mutation()
        history.rebuild_after_edit(self.experiment)
        self.assertTrue(is_stale("mutation_counts"), "the edit does not pay for this")

        self.client.get("/dashboard")

        self.assertFalse(is_stale("mutation_counts"), "the dashboard does")

    # --- a rebuild that fails must not take the page with it ------------------------------

    def test_a_failing_rebuild_still_renders_the_page(self):
        """`ensure_fresh` logs, records `last_error` and returns False. A plugin whose rebuild
        raises degrades its own page; it does not 500 the dashboard."""
        from aledb_common.models import DerivedDataState
        from aledb_common.rebuild_registry import (
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
        state = DerivedDataState.objects.get(name="mutation_counts", ale_experiment=None)
        self.assertIn("no", state.last_error)
        self.assertIsNotNone(state.stale_since, "left stale so the next reader tries again")

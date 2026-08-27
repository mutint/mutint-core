"""Every derived table has to be rebuilt by whoever reads it.

`rebuild_registry` documents the contract -- `request_rebuild` marks, and the page that reads
the data calls `ensure_fresh` -- and for a long time exactly one reader honoured it. Six
rebuilders were registered; only `overview` refreshed itself. So an experiment filter change,
which deliberately marks and does not rebuild, left the needle plot, Fixed Mutations,
Convergence and the dashboard showing what was true under the previous cutoff, with nothing
short of `./aledb rebuild` that would ever correct them.

The sharpest case was one page: `/stats` renders `get_experiment_summary`, which refreshed, and
`get_needle_plot_data`, which did not -- two numbers derived from the same mutations,
disagreeing in the same viewport.

Each test here has the same shape: make the data current, change the mutations *behind* the
reader's back, mark stale the way a filter edit does, and read through the public entry point.
The needle-plot one goes further and reads the stored table directly first, so it shows that
nothing else rebuilt it and the reader is what corrected the answer -- an assertion that the
reader merely *returns* the right number would pass equally well if something upstream had
quietly done the work.
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

    def test_the_needle_plot_rebuilds_itself(self):
        from aledb_stats.models import StaticData
        from aledb_stats.util import get_needle_plot_data

        before = len(get_needle_plot_data(self.experiment.ale_id))
        self._add_a_mutation()
        request_rebuild(self.experiment.ale_id, reason='test')

        # Nothing has rebuilt it: marking is all a filter edit does, on purpose.
        stored = StaticData.objects.get(id=self.experiment.ale_id).mut_needle_data
        self.assertEqual(before, len(stored), "the stored table is still the old one")
        self.assertTrue(is_stale('static_data', self.experiment.ale_id))

        # Reading through the public entry point is what corrects it.
        self.assertEqual(before + 1, len(get_needle_plot_data(self.experiment.ale_id)))
        self.assertFalse(is_stale('static_data', self.experiment.ale_id))

    def test_the_needle_plot_and_the_overview_agree_on_one_page(self):
        """They are rendered together by `/stats`, so a difference in freshness between them
        is visible as two counts of the same thing disagreeing."""
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

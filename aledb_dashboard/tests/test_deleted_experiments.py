"""The installation-wide totals used to count what had been deleted.

Deletion here is soft -- `SoftDeleteMixin` stamps `deleted_at` and leaves everything below the
experiment in place -- and this codebase's managers are deliberately unfiltered, so
`MutationCall.objects.all()` and `Population.objects.count()` still see every project and
experiment anybody has ever removed. Nothing marked the totals stale on a delete either, so
even a rebuild would have produced the same numbers.
"""

from aledb_common.rebuild_registry import is_stale, run_rebuilds
from aledb_dashboard.models import InventoryCounts, MutationCallCounts
from aledb_dashboard.util import rebuild_mutation_counts, rebuild_sample_counts
from aledb_mutation_editor.tests.base import EditorTestCase


class DeletedExperimentTestCase(EditorTestCase):

    def _totals(self):
        rebuild_mutation_counts()
        rebuild_sample_counts()
        return (MutationCallCounts.objects.first().total,
                InventoryCounts.objects.first().population_count)

    def test_a_deleted_experiments_mutations_leave_the_totals(self):
        calls_before, _ = self._totals()
        self.assertEqual(4, calls_before, "the fixture's four calls")

        self.experiment.soft_delete(self.owner)

        calls_after, _ = self._totals()
        self.assertEqual(0, calls_after)

    def test_a_deleted_experiments_samples_leave_the_counts(self):
        _, ales_before = self._totals()
        self.assertEqual(1, ales_before)

        self.experiment.soft_delete(self.owner)

        _, ales_after = self._totals()
        self.assertEqual(0, ales_after)

    def test_deleting_the_project_counts_too(self):
        """Deleting a project does not stamp its experiments, so a check that looked only at
        `Experiment.deleted_at` would go on counting everything underneath it."""
        self._totals()

        self.experiment.project.soft_delete(self.owner)

        calls_after, ales_after = self._totals()
        self.assertEqual(0, calls_after)
        self.assertEqual(0, ales_after)

    # --- and the totals are told about it -------------------------------------------------

    def test_deleting_an_experiment_marks_the_totals_stale(self):
        run_rebuilds(self.experiment.id, force=True)
        self.assertFalse(is_stale("mutation_counts"))

        response = self.client.post(
            "/experiment/%d/delete/" % self.experiment.id, {})

        self.assertEqual(200, response.status_code)
        self.assertTrue(is_stale("mutation_counts"))

    def test_deleting_a_project_marks_the_totals_stale(self):
        run_rebuilds(self.experiment.id, force=True)
        self.assertFalse(is_stale("sample_counts"))

        response = self.client.post(
            "/project/%d/delete/" % self.experiment.project_id, {})

        self.assertEqual(200, response.status_code)
        self.assertTrue(is_stale("sample_counts"))

    def test_a_delete_does_not_invalidate_another_experiments_derived_data(self):
        """`request_rebuild()` with no experiment would mark every one of them. Removing one
        experiment cannot make another's derived data wrong.

        It needs an **experiment-scoped** rebuilder to watch, and core has none left to borrow:
        it watched `static_data` until the needle plot stopped being stored, then
        `experiment_filter` until the shared filter it defaulted stopped existing, and what
        remains -- `sample_counts` and `mutation_counts` -- is site-scoped and *is* legitimately
        marked by a delete, as the two tests above assert. So it registers its own, which is
        also the honest shape: the property belongs to the delete view, not to whichever
        rebuilder happened to be available.
        """
        from aledb_common.rebuild_registry import (
            register_rebuilder, request_rebuild, unregister_rebuilder,
        )

        register_rebuilder("test.other_experiment", lambda experiment_id: None)
        self.addCleanup(unregister_rebuilder, "test.other_experiment")
        other = self._second_experiment()
        run_rebuilds(other.id, force=True)
        self.assertFalse(is_stale("test.other_experiment", other.id))

        self.client.post("/experiment/%d/delete/" % self.experiment.id, {})

        self.assertFalse(is_stale("test.other_experiment", other.id),
                         "deleting one experiment marked another's derived data stale")

    def _second_experiment(self):
        from aledb_experiment.models import Experiment

        created = self.client.post(
            "/project/create/", {"name": "Other", "experiment": "Other"}).json()
        return Experiment.objects.get(pk=created["experiment_id"])

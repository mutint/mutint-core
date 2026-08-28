"""The installation-wide totals used to count what had been deleted.

Deletion here is soft -- `SoftDeleteMixin` stamps `deleted_at` and leaves everything below the
experiment in place -- and this codebase's managers are deliberately unfiltered, so
`ObservedMutation.objects.all()` and `AleId.objects.count()` still see every project and
experiment anybody has ever removed. Nothing marked the totals stale on a delete either, so
even a rebuild would have produced the same numbers.
"""

from aledb_common.rebuild_registry import is_stale, run_rebuilds
from aledb_dashboard.models import ObservedMutationCounts, SampleCounts
from aledb_dashboard.util import rebuild_mutation_counts, rebuild_sample_counts
from aledb_mutation_editor.tests.base import EditorTestCase


class DeletedExperimentTestCase(EditorTestCase):

    def _totals(self):
        rebuild_mutation_counts()
        rebuild_sample_counts()
        return (ObservedMutationCounts.objects.first().total,
                SampleCounts.objects.first().ale_count)

    def test_a_deleted_experiments_mutations_leave_the_totals(self):
        observed_before, _ = self._totals()
        self.assertEqual(4, observed_before, "the fixture's four observations")

        self.experiment.soft_delete(self.owner)

        observed_after, _ = self._totals()
        self.assertEqual(0, observed_after)

    def test_a_deleted_experiments_samples_leave_the_counts(self):
        _, ales_before = self._totals()
        self.assertEqual(1, ales_before)

        self.experiment.soft_delete(self.owner)

        _, ales_after = self._totals()
        self.assertEqual(0, ales_after)

    def test_deleting_the_project_counts_too(self):
        """Deleting a project does not stamp its experiments, so a check that looked only at
        `AleExperiment.deleted_at` would go on counting everything underneath it."""
        self._totals()

        self.experiment.project.soft_delete(self.owner)

        observed_after, ales_after = self._totals()
        self.assertEqual(0, observed_after)
        self.assertEqual(0, ales_after)

    # --- and the totals are told about it -------------------------------------------------

    def test_deleting_an_experiment_marks_the_totals_stale(self):
        run_rebuilds(self.experiment.ale_id, force=True)
        self.assertFalse(is_stale("mutation_counts"))

        response = self.client.post(
            "/ale/experiment/%d/delete/" % self.experiment.ale_id, {})

        self.assertEqual(200, response.status_code)
        self.assertTrue(is_stale("mutation_counts"))

    def test_deleting_a_project_marks_the_totals_stale(self):
        run_rebuilds(self.experiment.ale_id, force=True)
        self.assertFalse(is_stale("sample_counts"))

        response = self.client.post(
            "/ale/project/%d/delete/" % self.experiment.project_id, {})

        self.assertEqual(200, response.status_code)
        self.assertTrue(is_stale("sample_counts"))

    def test_a_delete_does_not_invalidate_another_experiments_derived_data(self):
        """`request_rebuild()` with no experiment would mark every one of them. Removing one
        experiment cannot make another's derived data wrong.

        Watches `experiment_filter`, the experiment-scoped rebuilder core still has; it
        watched `static_data` until the needle plot stopped being stored.
        """
        run_rebuilds(self.experiment.ale_id, force=True)

        self.client.post("/ale/experiment/%d/delete/" % self.experiment.ale_id, {})

        self.assertFalse(is_stale("experiment_filter", self.experiment.ale_id))

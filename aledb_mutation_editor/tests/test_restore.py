"""Reconstructing an earlier mutation set, and putting it back.

The scheme these exercise: a version is an edit set, the state at a version is derived by
undoing everything newer, and restoring is a *new* edit set rather than a rewind. The last
case in `SweptMutationTestCase` is the one that justifies storing `mutation_identity` at all.
"""

from decimal import Decimal

from aledb_import.ale_experiment import _delete_all_orphaned_mutations
from aledb_mutation_editor import history
from aledb_mutation_editor.models import (
    KIND_DELETE, MutationEdit, MutationEditSet,
)
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import Mutation, MutationCall


class RestoreTestCase(EditorTestCase):

    def _delete(self, sample, mutation, note="deleted"):
        call = MutationCall.objects.get(sample=sample,
                                                mutation=mutation)
        return history.apply_edits(self.experiment, self.owner, KIND_DELETE,
                                     removals=[call], note=note)

    def test_a_deleted_call_comes_back_field_for_field(self):
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_2)
        before = history.call_snapshot(call)

        self._delete(self.sample_a, self.mut_2)
        history.restore(self.experiment, self.owner, None)

        restored = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_2)
        self.assertEqual(before, history.call_snapshot(restored))
        self.assertEqual(Decimal("0.7500"), restored.frequency)
        # `evidence` is the first snapshotted field that is not a scalar, so it rides through
        # the log as JSON inside JSON -- `MutationEdit.snapshot` is itself a JSONField. A
        # restore that put back the key holding a string of a dict would satisfy the
        # whole-snapshot comparison above and still be wrong on the way out.
        self.assertEqual({"wt_reads": 10, "mutated_reads": 30}, restored.evidence)

    def test_the_restore_is_itself_a_edit_set(self):
        deletion = self._delete(self.sample_a, self.mut_2)
        restore = history.restore(self.experiment, self.owner, deletion)

        self.assertIsNone(restore, "restoring to the delete's own state is a no-op")

        restore = history.restore(self.experiment, self.owner, None)
        self.assertIsNotNone(restore)
        self.assertEqual("restore", restore.kind)
        self.assertIsNone(restore.restored_to, "None means the pre-edit state")
        self.assertEqual(2, MutationEditSet.objects.count(),
                         "the delete and the restore; the delete is not rewritten")

    def test_restoring_to_a_named_point_records_which(self):
        first = self._delete(self.sample_a, self.mut_2)
        self._delete(self.sample_a, self.mut_3)

        restore = history.restore(self.experiment, self.owner, first)

        self.assertEqual(first, restore.restored_to)
        self.assertIn("#%d" % first.pk, restore.note)

    def test_restoring_to_the_middle_of_a_history_gives_that_exact_state(self):
        self._delete(self.sample_a, self.mut_1)
        second = self._delete(self.sample_a, self.mut_2)
        self._delete(self.sample_a, self.mut_3)
        self.assertEqual(set(), self.call_ids(self.sample_a))

        history.restore(self.experiment, self.owner, second)

        # After the second delete, mut_1 and mut_2 were gone and mut_3 was still there.
        self.assertEqual({self.mut_3.id}, self.call_ids(self.sample_a))

    def test_restoring_to_before_everything_gives_the_imported_set(self):
        self._delete(self.sample_a, self.mut_1)
        self._delete(self.sample_a, self.mut_2)
        self._delete(self.sample_b, self.mut_1)

        history.restore(self.experiment, self.owner, None)

        self.assertEqual({self.mut_1.id, self.mut_2.id, self.mut_3.id},
                         self.call_ids(self.sample_a))
        self.assertEqual({self.mut_1.id}, self.call_ids(self.sample_b))

    def test_restoring_twice_to_the_same_point_does_nothing_the_second_time(self):
        """Otherwise the history fills with entries that changed nothing."""
        self._delete(self.sample_a, self.mut_2)
        self.assertIsNotNone(history.restore(self.experiment, self.owner, None))
        self.assertIsNone(history.restore(self.experiment, self.owner, None))

    def test_a_restore_can_itself_be_restored_past(self):
        """By the time you look back at it, a restore is just another edit set with rows."""
        deletion = self._delete(self.sample_a, self.mut_2)
        history.restore(self.experiment, self.owner, None)
        self.assertEqual({self.mut_1.id, self.mut_2.id, self.mut_3.id},
                         self.call_ids(self.sample_a))

        history.restore(self.experiment, self.owner, deletion)
        self.assertEqual({self.mut_1.id, self.mut_3.id}, self.call_ids(self.sample_a))

    def test_the_log_is_never_rewritten(self):
        deletion = self._delete(self.sample_a, self.mut_2)
        edits_before = MutationEdit.objects.filter(edit_set=deletion).count()

        history.restore(self.experiment, self.owner, None)

        self.assertTrue(MutationEditSet.objects.filter(pk=deletion.pk).exists())
        self.assertEqual(edits_before,
                         MutationEdit.objects.filter(edit_set=deletion).count())


class PerSampleRestoreTestCase(EditorTestCase):

    def test_restoring_one_sample_leaves_the_others_alone(self):
        calls = list(MutationCall.objects.filter(sample=self.sample_a))
        calls += list(MutationCall.objects.filter(sample=self.sample_b))
        history.apply_edits(self.experiment, self.owner, KIND_DELETE, removals=calls,
                              note="cleared both")
        self.assertEqual(0, self.call_count())

        history.restore(self.experiment, self.owner, None, sample_ids=[self.sample_a.id])

        self.assertEqual({self.mut_1.id, self.mut_2.id, self.mut_3.id},
                         self.call_ids(self.sample_a))
        self.assertEqual(set(), self.call_ids(self.sample_b),
                         "sample B was not named, so it stays as it is")

    def test_the_note_says_it_was_partial(self):
        self._clear(self.sample_a)
        restore = history.restore(self.experiment, self.owner, None,
                                  sample_ids=[self.sample_a.id])
        self.assertIn("sample(s)", restore.note)

    def _clear(self, sample):
        calls = list(MutationCall.objects.filter(sample=sample))
        return history.apply_edits(self.experiment, self.owner, KIND_DELETE,
                                     removals=calls, note="cleared")


class SweptMutationTestCase(EditorTestCase):
    """Restoring after the orphan sweep has taken the Mutation row.

    `aledb_import.ale_experiment._delete_all_orphaned_mutations` hard-deletes every Mutation
    with no MutationCall, and it runs after an experiment delete and after `delete_sample`
    -- neither of which this app is involved in. So removing a mutation's last call can
    leave a Mutation that some later, unrelated operation destroys. This is the whole reason
    `MutationEdit.mutation_identity` exists rather than the log just holding a foreign key.
    """

    def test_the_sweep_takes_a_mutation_whose_last_call_was_deleted(self):
        """Establishes the precondition; if this stops being true the rest is moot."""
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_2)
        history.apply_edits(self.experiment, self.owner, KIND_DELETE, removals=[call])

        _delete_all_orphaned_mutations()

        self.assertFalse(Mutation.objects.filter(pk=self.mut_2.pk).exists())

    def test_the_log_survives_the_sweep_with_its_foreign_key_nulled(self):
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_2)
        edit_set = history.apply_edits(self.experiment, self.owner, KIND_DELETE,
                                           removals=[call])
        _delete_all_orphaned_mutations()

        edit = edit_set.edits.get()
        edit.refresh_from_db()
        self.assertIsNone(edit.mutation_id)
        self.assertEqual(200, edit.mutation_identity["position"])

    def test_restore_recreates_the_mutation_and_the_call(self):
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_2)
        before = history.call_snapshot(call)
        history.apply_edits(self.experiment, self.owner, KIND_DELETE, removals=[call])
        _delete_all_orphaned_mutations()

        history.restore(self.experiment, self.owner, None)

        recreated = Mutation.objects.get(experiment=self.experiment, position=200)
        self.assertNotEqual(self.mut_2.pk, recreated.pk,
                            "a swept row cannot come back under its old id")
        self.assertEqual("C>G", recreated.sequence_change)
        # Recreated through the same get_or_create key the importer uses, carrying the two
        # JSON columns -- so it renders and round-trips to a .gd line exactly as before.
        self.assertEqual(self.mut_2.gd_data, recreated.gd_data)
        self.assertEqual(self.mut_2.annotation, recreated.annotation)

        restored = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=recreated)
        self.assertEqual(before, history.call_snapshot(restored))

    def test_it_does_not_mint_a_second_row_for_a_mutation_that_survived(self):
        """mut_1 is observed in both samples, so deleting one call does not orphan it."""
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_1)
        history.apply_edits(self.experiment, self.owner, KIND_DELETE, removals=[call])
        _delete_all_orphaned_mutations()

        history.restore(self.experiment, self.owner, None)

        self.assertEqual(1, Mutation.objects.filter(experiment=self.experiment,
                                                    position=100).count())

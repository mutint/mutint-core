"""Applying a change, and what the log records about it.

The snapshot is what these mostly assert. An edit log that recorded *that* a mutation was
deleted but not what its frequency or the caller's evidence were would let you see the history and not
undo it, which is most of the value gone.
"""

from decimal import Decimal

from aledb_common.models import DerivedDataState
from aledb_mutation_editor import history
from aledb_mutation_editor.models import (
    KIND_DELETE, OP_ADD, OP_REMOVE, MutationEdit, MutationEditSet,
)
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import MutationCall


class ApplyEditsTestCase(EditorTestCase):

    def _delete(self, sample, mutation):
        call = MutationCall.objects.get(sample=sample,
                                                mutation=mutation)
        return history.apply_edits(self.experiment, self.owner, KIND_DELETE,
                                     removals=[call], note="test delete")

    def test_a_delete_removes_the_row(self):
        self._delete(self.sample_a, self.mut_2)

        self.assertFalse(MutationCall.objects.filter(
            sample=self.sample_a, mutation=self.mut_2).exists())
        self.assertEqual({self.mut_1.id, self.mut_3.id}, self.call_ids(self.sample_a))

    def test_it_leaves_the_mutation_row_alone(self):
        """The whole design rests on this.

        Mutation ids are stored as bare integers in aledb-converge, aledb-phylogeny and every
        exported CSV. Deleting the row and letting a re-import recreate it would mint a new pk
        for the same biological mutation and quietly invalidate all of them.
        """
        self._delete(self.sample_a, self.mut_2)
        self.mut_2.refresh_from_db()
        self.assertEqual(200, self.mut_2.position)

    def test_it_leaves_other_samples_alone(self):
        self._delete(self.sample_a, self.mut_1)
        self.assertEqual({self.mut_1.id}, self.call_ids(self.sample_b))

    def test_one_edit_set_per_call_however_many_rows(self):
        calls = list(MutationCall.objects.filter(sample=self.sample_a))
        history.apply_edits(self.experiment, self.owner, KIND_DELETE, removals=calls,
                              note="all three")

        self.assertEqual(1, MutationEditSet.objects.count())
        self.assertEqual(3, MutationEdit.objects.count())

    def test_nothing_to_do_writes_no_edit_set(self):
        """An empty edit set would be a history entry saying nothing happened."""
        self.assertIsNone(
            history.apply_edits(self.experiment, self.owner, KIND_DELETE))
        self.assertEqual(0, MutationEditSet.objects.count())

    def test_the_edit_set_records_who_and_what(self):
        edit_set = self._delete(self.sample_a, self.mut_2)

        self.assertEqual(self.owner, edit_set.created_by)
        self.assertEqual(KIND_DELETE, edit_set.kind)
        self.assertEqual("test delete", edit_set.note)
        self.assertIsNotNone(edit_set.created_at)
        self.assertFalse(edit_set.is_system)

    def test_the_change_names_the_sample_and_the_mutation(self):
        edit_set = self._delete(self.sample_a, self.mut_2)
        edit = edit_set.edits.get()

        self.assertEqual(OP_REMOVE, edit.operation)
        self.assertEqual(self.sample_a.id, edit.sample_id)
        self.assertEqual(self.mut_2.id, edit.mutation_id)
        self.assertIsNone(edit.source_sample_id)


class SnapshotTestCase(EditorTestCase):

    def test_every_mutation_call_column_is_captured(self):
        """A removal has to be undoable exactly, not approximately."""
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=self.mut_1)
        snapshot = history.call_snapshot(call)

        self.assertEqual(set(history.CALL_FIELDS), set(snapshot))
        self.assertEqual(True, snapshot["present"])
        self.assertEqual("breseq", snapshot["source"])
        # Whole, not merely present: `evidence` is a JSON column, so a snapshot that stored
        # the key and dropped what was under it would still satisfy the field-set check above.
        self.assertEqual({"wt_reads": 10, "mutated_reads": 30}, snapshot["evidence"])

    def test_frequency_survives_as_a_decimal_not_a_float(self):
        """These columns keep four decimal places, which is where a float round trip moves."""
        call = self.observe(self.sample_b, self.mut_2, frequency="0.1234")
        snapshot = history.call_snapshot(call)

        self.assertEqual("0.1234", snapshot["frequency"])
        self.assertIsInstance(snapshot["frequency"], str)
        self.assertEqual(Decimal("0.1234"),
                         history._call_kwargs(snapshot)["frequency"])

    def test_the_mutation_identity_is_the_importers_get_or_create_key(self):
        """If these drift apart, a restore mints a second row for one mutation."""
        identity = history.mutation_identity(self.mut_1)

        for field in history.MUTATION_KEY_FIELDS:
            self.assertIn(field, identity)
        self.assertEqual(100, identity["position"])
        self.assertEqual("NC_000913", identity["reseq_reference"])
        # Carried so a recreated row renders and round-trips to a .gd line as before.
        self.assertEqual(self.mut_1.gd_data, identity["gd_data"])
        self.assertEqual(self.mut_1.annotation, identity["annotation"])

    def test_a_key_from_a_row_and_from_its_logged_identity_agree(self):
        """The log's identity comes back through JSON; the live one does not."""
        identity = history.mutation_identity(self.mut_1)
        self.assertEqual(history.mutation_key(self.mut_1),
                         history.key_from_identity(identity))


class RebuildTestCase(EditorTestCase):

    def test_editing_marks_the_derived_data_stale(self):
        """An edit that did not mark what is derived from these rows would leave pages
        disagreeing about the same mutations.

        Registers its own rebuilder, because core has none left that is experiment-scoped:
        fixation and convergence compute on read, the needle plot and the Overview counts store
        nothing, and `experiment_filter` went with the shared filter row. An installed plugin
        is what contributes one now, so the test contributes one too.
        """
        from aledb_common.rebuild_registry import register_rebuilder, unregister_rebuilder

        register_rebuilder("test.derived", lambda experiment_id: None)
        self.addCleanup(unregister_rebuilder, "test.derived")

        history.rebuild_after_edit(self.experiment)

        self.assertTrue(DerivedDataState.objects.filter(
            name="test.derived", experiment=self.experiment).exists())

    def test_the_dashboard_totals_are_marked_stale_but_not_rebuilt(self):
        """Site-scoped, and deliberately not narrowed away -- but not run here either.

        `rebuild_after_structural_change` refuses to pay for these because a renumber cannot
        change a mutation count. Deleting a mutation can, so it has to be *marked*. Running it
        is a different question: it recounts every MutationCall in the installation, which
        is 4.9 seconds of read on a 74,859-row database and not a bill a single delete should
        pick up. The dashboard calls `ensure_fresh` and pays it once on its next view.

        Asserted from a fresh row rather than an absent one: a missing row already counts as
        stale, so `assertTrue(is_stale(...))` on a clean database passes without the code
        doing anything at all.
        """
        from aledb_common.rebuild_registry import is_stale

        DerivedDataState.objects.update_or_create(
            name="mutation_counts", experiment=None,
            defaults={"stale_since": None})

        history.rebuild_after_edit(self.experiment)

        self.assertTrue(is_stale("mutation_counts"),
                        "a mutation edit invalidates the installation-wide totals")
        state = DerivedDataState.objects.get(name="mutation_counts", experiment=None)
        self.assertIsNotNone(state.stale_since, "marked, and left for the dashboard to run")

    def test_the_experiments_own_derived_data_is_rebuilt_here(self):
        """The other half of the same split: what the caller just changed is made warm now,
        because it is already paying for a long operation and is about to look at it.

        Experiment-scoped, which is the half that runs -- the site-wide totals are marked and
        left for the dashboard, as the test above asserts. Registers its own rebuilder for the
        reason the one above does: core has no experiment-scoped one left.
        """
        from aledb_common.rebuild_registry import (
            is_stale, register_rebuilder, unregister_rebuilder,
        )

        register_rebuilder("test.warmed", lambda experiment_id: None)
        self.addCleanup(unregister_rebuilder, "test.warmed")

        history.rebuild_after_edit(self.experiment)

        self.assertFalse(is_stale("test.warmed", self.experiment.id))

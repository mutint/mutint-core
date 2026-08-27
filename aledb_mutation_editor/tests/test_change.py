"""Changing a mutation's own fields, everywhere it is observed.

The operation is the mutation, not a set of samples: a `Mutation` is experiment-scoped and
shared by every sample observing it, so correcting a mis-called position is one correction.
The fixture's `mut_1` is in both samples and `mut_2` in one, which is the difference that
matters -- an edit has to take every observation with it, however many there are.
"""

from decimal import Decimal

from aledb_mutation_editor import history
from aledb_mutation_editor.models import (
    KIND_EDIT, MutationChange, MutationChangeSet,
)
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import Mutation, ObservedMutation

PAGE = "/mutation-editor/change"
APPLY = "/mutation-editor/change/apply"


class ChangeMutationTestCase(EditorTestCase):

    def change(self, mutation=None, **fields):
        payload = {
            "experiment_id": self.experiment.ale_id,
            "mutation_id": (mutation or self.mut_1).id,
            "mutation_type": "SNP",
            "seq_id": "NC_000913",
            "position": 100,
            "new_seq": "T",
        }
        payload.update(fields)
        return self.client.post(APPLY, payload)

    # --- the page -------------------------------------------------------------------------

    def test_the_page_opens_on_what_is_stored(self):
        response = self.client.get(PAGE, {"ale_experiment_id": self.experiment.ale_id,
                                          "mutation_id": self.mut_1.id})
        html = response.content.decode("utf-8")

        self.assertEqual(200, response.status_code)
        self.assertIn('"position": 100', html)
        self.assertIn('id="mutation-initial"', html)

    def test_the_page_says_how_many_samples_follow(self):
        html = self.client.get(PAGE, {"ale_experiment_id": self.experiment.ale_id,
                                      "mutation_id": self.mut_1.id}).content.decode("utf-8")

        self.assertIn("<b>2</b> sample", html)

    def test_a_mutation_from_another_experiment_is_not_found(self):
        """Scoped through the experiment, for the reason mutation_delete scopes its ids: a
        mutation belongs to one experiment's reference genome."""
        created = self.client.post(
            "/ale/projects/create/", {"name": "P2", "experiment": "E2"}).json()
        from aledb_experiment.models import AleExperiment

        other = AleExperiment.objects.get(pk=created["experiment_id"])
        stranger = self.make_mutation(position=1, sequence_change="A>C", experiment=other)

        response = self.client.get(PAGE, {"ale_experiment_id": self.experiment.ale_id,
                                          "mutation_id": stranger.id})

        self.assertEqual(404, response.status_code)

    # --- what it does ---------------------------------------------------------------------

    def test_every_sample_carrying_it_follows(self):
        response = self.change(position=150)

        self.assertEqual(200, response.status_code, response.content)
        self.mut_1.refresh_from_db()
        self.assertEqual(150, self.mut_1.position)
        self.assertEqual(2, ObservedMutation.objects.filter(mutation=self.mut_1).count())

    def test_the_mutation_keeps_its_primary_key(self):
        """The whole reason the row is updated rather than re-created. Mutation ids are stored
        as bare integers in aledb-converge, in aledb-phylogeny's JSON and in every exported
        CSV, and aledb-phylogeny is not on the rebuild hook."""
        before = self.mut_1.pk

        self.change(position=150)

        self.mut_1.refresh_from_db()
        self.assertEqual(before, self.mut_1.pk)
        self.assertEqual(1, Mutation.objects.filter(pk=before).count())

    def test_each_sample_keeps_its_own_observation(self):
        """What moves is what the mutation *is*. A frequency belongs to the sample."""
        ObservedMutation.objects.filter(sequencing_experiment=self.sample_b,
                                        mutation=self.mut_1).update(frequency=Decimal("0.25"))

        self.change(position=150)

        frequencies = {observed.sequencing_experiment_id: observed.frequency
                       for observed in
                       ObservedMutation.objects.filter(mutation=self.mut_1)}
        self.assertEqual(Decimal("0.7500"), frequencies[self.sample_a.id])
        self.assertEqual(Decimal("0.2500"), frequencies[self.sample_b.id])

    def test_a_mutation_in_one_sample_behaves_the_same(self):
        self.change(mutation=self.mut_2, position=250, new_seq="G")

        self.mut_2.refresh_from_db()
        self.assertEqual(250, self.mut_2.position)
        self.assertEqual(1, ObservedMutation.objects.filter(mutation=self.mut_2).count())

    def test_the_type_can_change_too(self):
        self.change(mutation=self.mut_2, mutation_type="SUB", position=200, size=3,
                    new_seq="GGG")

        self.mut_2.refresh_from_db()
        self.assertEqual("SUB", self.mut_2.mutation_type)
        self.assertEqual(3, self.mut_2.feature_length)

    def test_the_stored_record_is_rewritten_too(self):
        """`gd_data` is what `to_gd_line()` round-trips for gdtools APPLY. A mutation whose
        columns moved and whose record did not would export as its old self."""
        self.change(position=150)

        self.mut_1.refresh_from_db()
        self.assertEqual(150, self.mut_1.gd_data["position"])

    # --- the log --------------------------------------------------------------------------

    def test_it_is_one_changeset_marked_as_an_edit(self):
        self.change(position=150)

        change_set = MutationChangeSet.objects.get()
        self.assertEqual(KIND_EDIT, change_set.kind)
        self.assertEqual(self.owner, change_set.created_by)

    def test_the_observations_are_logged_as_moving_between_identities(self):
        """Not bookkeeping. The log is keyed on (sample, mutation_key, source), so an edit
        that logged nothing would leave `state_after` computing a key no earlier entry
        recorded -- and a restore to before it would silently leave the edit in place."""
        self.change(position=150)

        removed = MutationChange.objects.filter(operation="remove")
        added = MutationChange.objects.filter(operation="add")
        self.assertEqual(2, removed.count())
        self.assertEqual(2, added.count())
        self.assertEqual({100}, {c.mutation_identity["position"] for c in removed})
        self.assertEqual({150}, {c.mutation_identity["position"] for c in added})

    def test_restoring_to_before_the_edit_undoes_it(self):
        self.change(position=150)

        history.restore(self.experiment, self.owner, change_set=None)

        positions = {observed.mutation.position
                     for observed in ObservedMutation.objects.all()}
        self.assertIn(100, positions)

    def test_a_restore_puts_it_back_in_every_sample(self):
        self.change(position=150)

        history.restore(self.experiment, self.owner, change_set=None)

        at_100 = ObservedMutation.objects.filter(mutation__position=100)
        self.assertEqual(2, at_100.count())

    def test_a_restore_mints_a_new_row_rather_than_moving_the_edited_one_back(self):
        """The one place an edit does not preserve the primary key, and it is not free to
        change: `plan_restore` produces additions carrying the *old* identity, and
        `_resolve_mutation` get_or_creates from an identity when the row it was handed no
        longer matches it. Teaching restore to move a row's fields back instead would mean
        `state_after` replaying mutation-level state, which nothing else needs.

        What must never happen is the earlier bug this replaced: `_resolve_mutation` used to
        accept any row whose pk still existed, so the restored observations went back onto
        the *edited* mutation and the restore reported success having undone nothing."""
        original = self.mut_1.pk
        self.change(position=150)

        history.restore(self.experiment, self.owner, change_set=None)

        restored = ObservedMutation.objects.filter(mutation__position=100).first().mutation
        self.assertNotEqual(original, restored.pk)
        self.assertFalse(
            ObservedMutation.objects.filter(mutation__pk=original).exists(),
            "the edited row is left with no observations, as a swept mutation would be")

    # --- refusals -------------------------------------------------------------------------

    def test_changing_nothing_is_refused(self):
        """Applied twice: the first is a real change, the second asks for what it already
        has. The fixture's own `gd_data` is deliberately sparse, so the round trip has to go
        through one real edit before "unchanged" is even expressible."""
        self.change(position=150)

        response = self.change(position=150)

        self.assertEqual(400, response.status_code)
        self.assertIn("already has", response.json()["error"])
        self.assertEqual(1, MutationChangeSet.objects.count())

    def test_an_edit_that_would_duplicate_another_mutation_is_refused(self):
        """Two rows sharing the six-field get_or_create key is a state the importer cannot
        produce and would resolve arbitrarily if it met one. Reached by moving mut_2 onto the
        values mut_1 is then asked to take -- `sequence_change` is derived, so two mutations
        collide only when everything that derives it agrees."""
        self.change(mutation=self.mut_2, position=150)

        response = self.change(mutation=self.mut_1, position=150)

        self.assertEqual(400, response.status_code)
        self.assertIn("already has", response.json()["error"])
        self.assertIn(str(self.mut_2.id), response.json()["error"])
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.position)

    def test_a_value_breseq_would_reject_is_refused_per_field(self):
        response = self.change(position=0)

        self.assertEqual(400, response.status_code)
        self.assertIn("position", response.json()["errors"])
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.position)

    def test_a_locked_experiment_refuses_the_change(self):
        from django.utils import timezone

        self.experiment.locked_at = timezone.now()
        self.experiment.save()

        response = self.change(position=150)

        self.assertEqual(403, response.status_code)
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.position)

    def test_a_reader_may_not_change_anything(self):
        from django.contrib.auth.models import User

        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        self.client.force_login(reader)

        response = self.change(position=150)

        self.assertEqual(403, response.status_code)
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.position)

    def test_a_mutation_nothing_observes_is_not_edited_in_silence(self):
        """`apply_mutation_edit` answers None rather than moving a row with no state to log."""
        orphan = self.make_mutation(position=777, sequence_change="A>C")

        result = history.apply_mutation_edit(
            self.experiment, self.owner, orphan,
            history.mutation_identity(orphan))

        self.assertIsNone(result)
        orphan.refresh_from_db()
        self.assertEqual(777, orphan.position)

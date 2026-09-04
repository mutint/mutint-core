"""Changing a mutation, in every sample that carries it or in the ones chosen.

Two paths, and which runs depends on two independent questions: does the whole set move, and
do the new values already name a mutation this experiment has? Only "the whole set, onto
values nothing else holds" moves the `Mutation` row itself and keeps its primary key.
Everything else moves the chosen calls *off* it and onto a different row, leaving the
samples that were not chosen where they were.

The fixture's `mut_1` is in both samples and `mut_2` in one, which is the difference that
matters -- an edit has to take every chosen call with it, however many there are, and
a subset of two is the smallest subset there is.
"""

import json
from decimal import Decimal

from aledb_mutation_editor import history
from aledb_mutation_editor.models import (
    KIND_EDIT, MutationEdit, MutationEditSet,
)
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_sample.models import Mutation, MutationCall

PAGE = "/mutation-editor/edit"
APPLY = "/mutation-editor/edit/apply"


class ChangeMutationTestCase(EditorTestCase):

    def edit(self, mutation=None, target_sample_ids=None, **fields):
        """POST the change form. `target_sample_ids` left out is the endpoint's older contract,
        "every sample carrying it", and is what most of this file exercises."""
        payload = {
            "experiment_id": self.experiment.id,
            "mutation_id": (mutation or self.mut_1).id,
            "mutation_type": "SNP",
            "seq_id": "NC_000913",
            "position": 100,
            "new_seq": "T",
        }
        if target_sample_ids is not None:
            payload["target_sample_ids"] = json.dumps(target_sample_ids)
        payload.update(fields)
        return self.client.post(APPLY, payload)

    # --- the page -------------------------------------------------------------------------

    def test_the_page_opens_on_what_is_stored(self):
        response = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                          "mutation_id": self.mut_1.id})
        html = response.content.decode("utf-8")

        self.assertEqual(200, response.status_code)
        self.assertIn('"position": 100', html)
        self.assertIn('id="mutation-initial"', html)

    def test_the_page_says_how_many_samples_follow(self):
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "mutation_id": self.mut_1.id}).content.decode("utf-8")

        self.assertIn("<b>2</b> that carr", html)

    def test_the_page_offers_only_the_samples_carrying_it(self):
        """There is nothing to change in a sample that does not carry the mutation, and
        `data-value` is what `aledbSelectList` reads a row's id off -- a list rendered without
        it looks right and posts an empty selection."""
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "mutation_id": self.mut_2.id}).content.decode("utf-8")

        self.assertIn('data-value="%d"' % self.sample_a.id, html)
        self.assertNotIn('data-value="%d"' % self.sample_b.id, html)

    def test_it_opens_on_the_sample_the_link_came_from(self):
        """The per-sample table's `change` link carries `?sample_id=`, and correcting a call
        you are looking at in the sample you are looking at it in is what following it means.
        `active` on the <li> is the selection, so that is what has to be there."""
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "mutation_id": self.mut_1.id,
                                      "sample_id": self.sample_b.id}).content.decode("utf-8")

        self.assertIn('data-value="%d" class="active"' % self.sample_b.id, html)
        self.assertIn('<li data-value="%d">' % self.sample_a.id, html)

    def test_it_opens_on_every_sample_when_the_link_names_none(self):
        """The grid's link, whose row spans every sample -- so there is no one sample that was
        clicked and the whole set is the honest default."""
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "mutation_id": self.mut_1.id}).content.decode("utf-8")

        for sample in (self.sample_a, self.sample_b):
            self.assertIn('data-value="%d" class="active"' % sample.id, html)

    def test_sample_id_all_opens_on_every_sample(self):
        """`?sample_id=all` is the editor's whole-experiment sentinel. It is not a sample, so it
        lands on the default rather than on an empty selection."""
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "mutation_id": self.mut_1.id,
                                      "sample_id": "all"}).content.decode("utf-8")

        for sample in (self.sample_a, self.sample_b):
            self.assertIn('data-value="%d" class="active"' % sample.id, html)

    def test_a_sample_that_does_not_carry_it_is_not_preselected(self):
        """A link naming a sample this mutation is not in cannot select nothing: the page
        would open with a Save button that refuses."""
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "mutation_id": self.mut_2.id,
                                      "sample_id": self.sample_b.id}).content.decode("utf-8")

        self.assertIn('data-value="%d" class="active"' % self.sample_a.id, html)

    def test_a_mutation_from_another_experiment_is_not_found(self):
        """Scoped through the experiment, for the reason mutation_delete scopes its ids: a
        mutation belongs to one experiment's reference genome."""
        created = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        from aledb_experiment.models import Experiment

        other = Experiment.objects.get(pk=created["experiment_id"])
        stranger = self.make_mutation(position=1, sequence_change="A>C", experiment=other)

        response = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                          "mutation_id": stranger.id})

        self.assertEqual(404, response.status_code)

    # --- what it does ---------------------------------------------------------------------

    def test_every_sample_carrying_it_follows(self):
        response = self.edit(position=150)

        self.assertEqual(200, response.status_code, response.content)
        self.mut_1.refresh_from_db()
        self.assertEqual(150, self.mut_1.start_position)
        self.assertEqual(2, MutationCall.objects.filter(mutation=self.mut_1).count())

    def test_the_mutation_keeps_its_primary_key(self):
        """The whole reason the row is updated rather than re-created. Mutation ids are stored
        as bare integers in aledb-phylogeny's JSON and in every exported CSV, and nothing
        refreshes them -- aledb-phylogeny discards the JSON rather than correcting it, and an
        exported CSV cannot be reached at all."""
        before = self.mut_1.pk

        self.edit(position=150)

        self.mut_1.refresh_from_db()
        self.assertEqual(before, self.mut_1.pk)
        self.assertEqual(1, Mutation.objects.filter(pk=before).count())

    def test_each_sample_keeps_its_own_call(self):
        """What moves is what the mutation *is*. A frequency belongs to the sample."""
        MutationCall.objects.filter(sample=self.sample_b,
                                        mutation=self.mut_1).update(frequency=Decimal("0.25"))

        self.edit(position=150)

        frequencies = {call.sample_id: call.frequency
                       for call in
                       MutationCall.objects.filter(mutation=self.mut_1)}
        self.assertEqual(Decimal("0.7500"), frequencies[self.sample_a.id])
        self.assertEqual(Decimal("0.2500"), frequencies[self.sample_b.id])

    def test_a_mutation_in_one_sample_behaves_the_same(self):
        self.edit(mutation=self.mut_2, position=250, new_seq="G")

        self.mut_2.refresh_from_db()
        self.assertEqual(250, self.mut_2.start_position)
        self.assertEqual(1, MutationCall.objects.filter(mutation=self.mut_2).count())

    def test_the_type_can_change_too(self):
        self.edit(mutation=self.mut_2, mutation_type="SUB", position=200, size=3,
                    new_seq="GGG")

        self.mut_2.refresh_from_db()
        self.assertEqual("SUB", self.mut_2.mutation_type)
        self.assertEqual(3, self.mut_2.feature_length)

    def test_the_stored_record_is_rewritten_too(self):
        """`gd_data` is what `to_gd_line()` round-trips for gdtools APPLY. A mutation whose
        columns moved and whose record did not would export as its old self."""
        self.edit(position=150)

        self.mut_1.refresh_from_db()
        self.assertEqual(150, self.mut_1.genome_diff["position"])

    # --- the log --------------------------------------------------------------------------

    def test_it_is_one_edit_set_marked_as_an_edit(self):
        self.edit(position=150)

        edit_set = MutationEditSet.objects.get()
        self.assertEqual(KIND_EDIT, edit_set.kind)
        self.assertEqual(self.owner, edit_set.created_by)

    def test_the_calls_are_logged_as_moving_between_identities(self):
        """Not bookkeeping. The log is keyed on (sample, mutation_key, source), so an edit
        that logged nothing would leave `state_after` computing a key no earlier entry
        recorded -- and a restore to before it would silently leave the edit in place."""
        self.edit(position=150)

        removed = MutationEdit.objects.filter(operation="remove")
        added = MutationEdit.objects.filter(operation="add")
        self.assertEqual(2, removed.count())
        self.assertEqual(2, added.count())
        self.assertEqual({100}, {c.mutation_identity["start_position"] for c in removed})
        self.assertEqual({150}, {c.mutation_identity["start_position"] for c in added})

    def test_restoring_to_before_the_edit_undoes_it(self):
        self.edit(position=150)

        history.restore(self.experiment, self.owner, edit_set=None)

        positions = {call.mutation.start_position
                     for call in MutationCall.objects.all()}
        self.assertIn(100, positions)

    def test_a_restore_puts_it_back_in_every_sample(self):
        self.edit(position=150)

        history.restore(self.experiment, self.owner, edit_set=None)

        at_100 = MutationCall.objects.filter(mutation__start_position=100)
        self.assertEqual(2, at_100.count())

    def test_a_restore_mints_a_new_row_rather_than_moving_the_edited_one_back(self):
        """The one place an edit does not preserve the primary key, and it is not free to
        change: `plan_restore` produces additions carrying the *old* identity, and
        `_resolve_mutation` get_or_creates from an identity when the row it was handed no
        longer matches it. Teaching restore to move a row's fields back instead would mean
        `state_after` replaying mutation-level state, which nothing else needs.

        What must never happen is the earlier bug this replaced: `_resolve_mutation` used to
        accept any row whose pk still existed, so the restored calls went back onto
        the *edited* mutation and the restore reported success having undone nothing."""
        original = self.mut_1.pk
        self.edit(position=150)

        history.restore(self.experiment, self.owner, edit_set=None)

        restored = MutationCall.objects.filter(mutation__start_position=100).first().mutation
        self.assertNotEqual(original, restored.pk)
        self.assertFalse(
            MutationCall.objects.filter(mutation__pk=original).exists(),
            "the edited row is left with no calls, as a swept mutation would be")

    # --- a subset of the samples ----------------------------------------------------------

    def test_only_the_chosen_samples_move(self):
        """The point of the whole thing: a call right in one sample and wrong in another is
        one correction to make, not a delete and a retype."""
        response = self.edit(position=150, target_sample_ids=[self.sample_b.id])

        self.assertEqual(200, response.status_code, response.content)
        moved = MutationCall.objects.get(sample=self.sample_b,
                                             mutation__start_position=150)
        stayed = MutationCall.objects.get(sample=self.sample_a,
                                              mutation=self.mut_1)
        self.assertNotEqual(self.mut_1.pk, moved.mutation_id)
        self.assertEqual(self.mut_1.pk, stayed.mutation_id)

    def test_the_row_they_came_off_is_left_alone(self):
        """It has not changed. Its remaining samples still observe the call it always was --
        and its primary key still means that to every exported CSV holding it."""
        self.edit(position=150, target_sample_ids=[self.sample_b.id])

        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.start_position)
        self.assertEqual(1, MutationCall.objects.filter(mutation=self.mut_1).count())

    def test_a_subset_mints_a_row_when_the_values_are_new(self):
        self.edit(position=150, target_sample_ids=[self.sample_b.id])

        minted = Mutation.objects.get(experiment=self.experiment, start_position=150)
        self.assertNotEqual(self.mut_1.pk, minted.pk)
        self.assertEqual("SNP", minted.mutation_type)
        self.assertEqual("NC_000913", minted.seq_id)

    def test_a_subset_joins_a_row_that_already_holds_the_values(self):
        """`mutation_for_identity` get_or_creates on the six fields `gd_import` keys on, so
        "the existing row if these values name one, a new row otherwise" is one question with
        one answer rather than two code paths."""
        self.edit(mutation=self.mut_2, position=150)
        self.mut_2.refresh_from_db()

        response = self.edit(mutation=self.mut_1, position=150,
                               target_sample_ids=[self.sample_b.id])

        self.assertEqual(self.mut_2.pk, response.json()["mutation_id"])
        self.assertEqual(2, MutationCall.objects.filter(mutation=self.mut_2).count())
        self.assertEqual(1, Mutation.objects.filter(experiment=self.experiment,
                                                    start_position=150).count())

    def test_the_minted_row_carries_the_new_record_and_the_old_one_keeps_its_own(self):
        """`gd_data` is what `to_gd_line()` round-trips for gdtools APPLY, so the two rows
        have to disagree about it -- the mutation the unchosen samples were left on has not
        changed, and re-annotating it as though it had is the bug this pins."""
        self.edit(position=150, target_sample_ids=[self.sample_b.id])

        minted = Mutation.objects.get(experiment=self.experiment, start_position=150)
        self.mut_1.refresh_from_db()
        self.assertEqual(150, minted.genome_diff["position"])
        self.assertEqual(100, self.mut_1.genome_diff["position"])

    def test_a_sample_that_already_carries_the_target_is_not_given_a_second_copy(self):
        """It loses the old call and keeps the call it already had, frequency and read
        counts included -- and is named back, because that is the one part of the result a
        person cannot read off the page."""
        self.observe(self.sample_b, self.mut_2, frequency="0.1000")
        # mut_2 goes through the form first so its stored identity is the shape the form
        # produces: the fixture writes `sequence_change` by hand as "C>G", which
        # `synthesize_sequence_change` never emits, so the two could not otherwise meet.
        self.edit(mutation=self.mut_2, position=200, new_seq="G")

        response = self.edit(mutation=self.mut_1, position=200, new_seq="G",
                               target_sample_ids=[self.sample_b.id])

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual([self.sample_b.label], response.json()["already"])
        calls = MutationCall.objects.filter(sample=self.sample_b,
                                                       mutation=self.mut_2)
        self.assertEqual(1, calls.count())
        self.assertEqual(Decimal("0.1000"), calls.first().frequency)
        self.assertFalse(MutationCall.objects.filter(
            sample=self.sample_b, mutation=self.mut_1).exists())

    def test_a_subset_is_one_edit_set_of_removals_and_additions(self):
        self.edit(position=150, target_sample_ids=[self.sample_b.id])

        edit_set = MutationEditSet.objects.get()
        self.assertEqual(KIND_EDIT, edit_set.kind)
        self.assertEqual(1, edit_set.edits.filter(operation="remove").count())
        self.assertEqual(1, edit_set.edits.filter(operation="add").count())

    def test_restoring_across_a_subset_change_reuses_the_original_row(self):
        """The opposite of the whole-set path, and it falls out rather than being arranged:
        the row never moved, so `_resolve_mutation` finds it still holding the old identity
        and hands the call straight back to the same primary key."""
        self.edit(position=150, target_sample_ids=[self.sample_b.id])

        history.restore(self.experiment, self.owner, edit_set=None)

        self.assertEqual(
            {self.mut_1.pk},
            set(MutationCall.objects.filter(mutation__start_position=100)
                .values_list("mutation_id", flat=True)))
        self.assertEqual(2, MutationCall.objects.filter(mutation=self.mut_1).count())

    def test_naming_every_carrying_sample_is_the_whole_set(self):
        """The two paths are decided on the *set*, not on whether the request named it."""
        before = self.mut_1.pk

        self.edit(position=150,
                    target_sample_ids=[self.sample_a.id, self.sample_b.id])

        self.mut_1.refresh_from_db()
        self.assertEqual(before, self.mut_1.pk)
        self.assertEqual(150, self.mut_1.start_position)

    def test_naming_no_samples_still_means_all_of_them(self):
        """The endpoint's contract before it could take a subset, which callers that do not
        care about samples still post."""
        before = self.mut_1.pk

        response = self.edit(position=150)

        self.assertEqual(200, response.status_code, response.content)
        self.mut_1.refresh_from_db()
        self.assertEqual(before, self.mut_1.pk)
        self.assertEqual(2, MutationCall.objects.filter(mutation=self.mut_1).count())

    # --- refusals -------------------------------------------------------------------------

    def test_a_sample_that_does_not_carry_it_is_refused(self):
        """Scoped to the samples carrying it for the reason the mutation is scoped to the
        experiment: a hand-typed id must not reach past what the page offered."""
        response = self.edit(mutation=self.mut_2, position=250,
                               target_sample_ids=[self.sample_b.id])

        self.assertEqual(404, response.status_code)
        self.assertIn("do not carry", response.json()["error"])
        self.mut_2.refresh_from_db()
        self.assertEqual(200, self.mut_2.start_position)

    def test_changing_nothing_is_refused(self):
        """Applied twice: the first is a real change, the second asks for what it already
        has. The fixture's own `gd_data` is deliberately sparse, so the round trip has to go
        through one real edit before "unchanged" is even expressible."""
        self.edit(position=150)

        response = self.edit(position=150)

        self.assertEqual(400, response.status_code)
        self.assertIn("already has", response.json()["error"])
        self.assertEqual(1, MutationEditSet.objects.count())

    def test_a_change_onto_values_another_mutation_holds_joins_it(self):
        """Two rows sharing the six-field get_or_create key is a state the importer cannot
        produce, so this cannot mint a second row -- but joining the existing one is a real
        correction and used to be refused outright. Reached by moving mut_2 onto the values
        mut_1 is then asked to take; `sequence_change` is derived, so two mutations meet only
        when everything deriving it agrees.

        The emptied row is left in place rather than deleted, which is what delete does with a
        Mutation as well -- and is what lets a restore hand the calls back to the same
        primary key."""
        self.edit(mutation=self.mut_2, position=150)
        self.mut_2.refresh_from_db()

        response = self.edit(mutation=self.mut_1, position=150)

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(self.mut_2.pk, response.json()["mutation_id"])
        # sample_a already carries mut_2, so it gets the removal and no addition: two
        # calls afterwards rather than three, and it is named back.
        self.assertEqual([self.sample_a.label], response.json()["already"])
        self.assertEqual(2, MutationCall.objects.filter(mutation=self.mut_2).count())
        self.assertFalse(MutationCall.objects.filter(mutation=self.mut_1).exists())
        self.assertTrue(Mutation.objects.filter(pk=self.mut_1.pk).exists())
        self.assertEqual(1, Mutation.objects.filter(experiment=self.experiment,
                                                    start_position=150).count(),
                         "joined the existing row rather than minting a second at 150")
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.start_position,
                         "the emptied row is left as it was, not moved as well")

    def test_a_value_breseq_would_reject_is_refused_per_field(self):
        response = self.edit(position=0)

        self.assertEqual(400, response.status_code)
        self.assertIn("position", response.json()["errors"])
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.start_position)

    def test_a_locked_experiment_refuses_the_change(self):
        from django.utils import timezone

        self.experiment.locked_at = timezone.now()
        self.experiment.save()

        response = self.edit(position=150)

        self.assertEqual(403, response.status_code)
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.start_position)

    def test_a_reader_may_not_change_anything(self):
        from django.contrib.auth.models import User

        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        self.client.force_login(reader)

        response = self.edit(position=150)

        self.assertEqual(403, response.status_code)
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.start_position)

    def test_a_mutation_nothing_observes_is_not_edited_in_silence(self):
        """`apply_mutation_edit` answers None rather than moving a row with no state to log."""
        orphan = self.make_mutation(position=777, sequence_change="A>C")

        result = history.apply_mutation_edit(
            self.experiment, self.owner, orphan,
            history.mutation_identity(orphan))

        self.assertIsNone(result)
        orphan.refresh_from_db()
        self.assertEqual(777, orphan.start_position)

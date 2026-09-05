"""What subtraction actually removes, and the four places it deliberately does not.

The rule is one sentence -- every mutation observed in the designated ancestor is removed from
every other sample, and the ancestor itself is removed from every listing -- and almost all of
this file is about its edges, because that is where a subtraction like this goes wrong: it is
invisible when it over-reaches and invisible when it under-reaches.
"""

from mutint_experiment.ancestor import (ancestral_mutation_ids, describe_ancestor,
                                       exclude_all_ancestry, exclude_ancestry, get_ancestor)
from mutint_mutation_editor.tests.base import EditorTestCase
from mutint_sample.models import MutationCall
from mutint_sample.util import (get_evolved_call_queryset, get_mutation_call_queryset,
                            get_reseq_ordered_dict, calls_for_samples)


class SubtractionTestCase(EditorTestCase):
    """`mut_1` is in both samples; `mut_2` and `mut_3` are in sample_a only."""

    def designate_a(self):
        self.experiment.set_ancestor(self.sample_a, self.owner)

    def evolved_mutation_ids(self):
        return set(get_evolved_call_queryset(self.experiment.id)
                   .values_list("mutation_id", flat=True))


class TestNothingHappensWithoutADesignation(SubtractionTestCase):

    def test_the_queryset_is_returned_untouched(self):
        """The common case must add no SQL, and must not be an empty `.exclude()` -- an empty
        Q handed to exclude() excludes everything, which once emptied a whole experiment."""
        raw = get_mutation_call_queryset(self.experiment.id)
        self.assertEqual(str(exclude_ancestry(raw, self.experiment.id).query),
                         str(raw.query))

    def test_every_sample_is_listed(self):
        self.assertEqual(set(get_reseq_ordered_dict(self.experiment.id)),
                         {self.sample_a.id, self.sample_b.id})

    def test_there_is_nothing_to_describe(self):
        self.assertIsNone(describe_ancestor(self.experiment.id))
        self.assertIsNone(get_ancestor(self.experiment.id))
        self.assertEqual(ancestral_mutation_ids(self.experiment.id), frozenset())


class TestSubtraction(SubtractionTestCase):

    def test_an_ancestral_mutation_leaves_every_other_sample(self):
        self.designate_a()
        # sample_b carried mut_1, and mut_1 is in the ancestor.
        self.assertNotIn(self.mut_1.id, self.evolved_mutation_ids())

    def test_the_ancestor_sample_leaves_the_calls(self):
        self.designate_a()
        remaining = set(get_evolved_call_queryset(self.experiment.id)
                        .values_list("sample_id", flat=True))
        self.assertNotIn(self.sample_a.id, remaining)

    def test_the_raw_queryset_still_has_everything(self):
        """`get_mutation_call_queryset` means "what is stored" and has to keep meaning it:
        the export, the editor and the per-sample page all depend on that."""
        self.designate_a()
        self.assertEqual(get_mutation_call_queryset(self.experiment.id).count(), 4)

    def test_a_mutation_missing_from_a_sample_subtracts_cleanly(self):
        """The miscall case, stated in the requirement: nothing requires an ancestral mutation
        to be present everywhere before it is subtracted. mut_2 and mut_3 are in the ancestor
        and in no other sample, and that is not an error."""
        self.designate_a()
        self.assertEqual(self.evolved_mutation_ids(), set())

    def test_a_mutation_the_ancestor_does_not_carry_survives(self):
        """The other half, and the one that would catch an over-broad exclusion."""
        evolved_only = self.make_mutation(position=999, sequence_change="T>C")
        self.observe(self.sample_b, evolved_only)
        self.designate_a()
        self.assertEqual(self.evolved_mutation_ids(), {evolved_only.id})


class TestListings(SubtractionTestCase):

    def test_the_ancestor_is_not_listed(self):
        self.designate_a()
        self.assertEqual(set(get_reseq_ordered_dict(self.experiment.id)),
                         {self.sample_b.id})

    def test_a_curation_page_can_still_ask_for_it(self):
        """The Edit-samples page and the mutation editor must still be able to change it."""
        self.designate_a()
        self.assertIn(self.sample_a.id,
                      get_reseq_ordered_dict(self.experiment.id, include_ancestor=True))

    def test_the_edit_samples_page_still_shows_it(self):
        self.designate_a()
        response = self.client.get("/experiment/%d/samples/" % self.experiment.id)
        self.assertContains(response, self.sample_a.source_name)

    def test_the_mutation_editor_still_shows_it(self):
        self.designate_a()
        response = self.client.get("/mutation-editor/",
                                   {"experiment_id": self.experiment.id})
        self.assertContains(response, self.sample_a.label)


class TestThePluginEntryPoint(SubtractionTestCase):
    """`calls_for_samples` is what mutint-converge, mutint-fixation and mutint-phylogeny
    derive from. Subtracting only the *sample* would leave an ancestral mutation in every ALE,
    which reports as convergent everywhere and fixed everywhere -- the loudest possible wrong
    answer, and the one this feature exists to remove."""

    def test_it_subtracts_the_mutations_not_merely_the_sample(self):
        self.designate_a()
        both = [self.sample_a.id, self.sample_b.id]
        ids = set(calls_for_samples(both, self.experiment.id)
                  .values_list("mutation_id", flat=True))
        self.assertNotIn(self.mut_1.id, ids)


class TestCrossExperiment(SubtractionTestCase):
    """Search, the interop API and the dashboard have no single experiment to name."""

    def test_it_subtracts_without_being_told_which_experiment(self):
        self.designate_a()
        ids = set(exclude_all_ancestry(MutationCall.objects.all())
                  .values_list("mutation_id", flat=True))
        self.assertEqual(ids, set())

    def test_another_experiments_rows_are_untouched(self):
        """Safe because `Mutation` rows are per experiment, so an id cannot cross."""
        other = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        from mutint_experiment.models import Experiment
        foreign = Experiment.objects.get(pk=other["experiment_id"])
        elsewhere = self.make_mutation(position=100, sequence_change="A>T",
                                       experiment=foreign)
        self.observe(self.sample_b, elsewhere)

        self.designate_a()
        ids = set(exclude_all_ancestry(MutationCall.objects.all())
                  .values_list("mutation_id", flat=True))
        self.assertEqual(ids, {elsewhere.id})


class TestDeletingTheAncestorSample(SubtractionTestCase):

    def test_it_marks_everything_stale(self):
        """`SET_NULL` clears the column silently, and that is the danger.

        Without the signal the designation vanishes while the derived data does not:
        mutint-phylogeny's cached trees would still have been inferred with these mutations
        subtracted, and convergence and fixation -- computed per request -- would flip back on
        the next page load. Half the site would disagree with the other half.
        """
        from mutint_common.rebuild_registry import (is_stale, register_rebuilder,
                                                   run_rebuilds, unregister_rebuilder)
        self.designate_a()
        register_rebuilder("test.ancestor_delete", lambda experiment_id: None)
        self.addCleanup(unregister_rebuilder, "test.ancestor_delete")

        # Start from fresh, so being stale afterwards can only have come from the deletion.
        run_rebuilds(self.experiment.id, only=["test.ancestor_delete"], force=True)
        self.assertFalse(is_stale("test.ancestor_delete", self.experiment.id))

        self.sample_a.delete()

        self.assertTrue(is_stale("test.ancestor_delete", self.experiment.id))

    def test_the_designation_goes_with_the_sample(self):
        from mutint_experiment.models import Experiment
        self.designate_a()
        self.sample_a.delete()
        self.assertIsNone(Experiment.objects.get(pk=self.experiment.pk).ancestor_id)

    def test_deleting_an_ordinary_sample_marks_nothing(self):
        """The signal has to be able to tell the difference, or every sample deletion in a
        cascade would mark the experiment stale."""
        from mutint_common.rebuild_registry import (is_stale, register_rebuilder,
                                                   run_rebuilds, unregister_rebuilder)
        self.designate_a()
        register_rebuilder("test.ordinary_delete", lambda experiment_id: None)
        self.addCleanup(unregister_rebuilder, "test.ordinary_delete")
        run_rebuilds(self.experiment.id, only=["test.ordinary_delete"], force=True)

        self.sample_b.delete()

        self.assertFalse(is_stale("test.ordinary_delete", self.experiment.id))

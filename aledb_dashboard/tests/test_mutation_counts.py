"""The dashboard's mutation totals, which count the installation rather than a view of it.

`rebuild_mutation_counts` used to run its rows through `filter_observed_mutations`, so the
site-wide totals were computed through whatever frequency cutoffs and ignored-gene lists each
experiment happened to carry. That is a category error twice over:

  * the dashboard is an inventory of what the installation *holds*, and a filter is one
    person's view of one experiment;
  * a filter is becoming per-user, and a shared table cannot be keyed by user -- so a filtered
    site-wide total is not merely wrong, it is not computable.

These tests pin that the filter does not reach here, that the counts are still buckets over
every live observation, and that `synonymous` / `nonsynonymous` are written at all -- the
branch filling them tested for token names that are not in `FUNCTIONAL_CHANGE_TYPE_LIST`, so
both columns had sat at zero since they were added.
"""

from django.test import TestCase

from aledb_dashboard.models import ObservedMutationCounts, UniqueMutationCounts
from aledb_dashboard.util import rebuild_mutation_counts
from aledb_experiment.models import (
    AleExperiment, AleId, Flask, FreezerBox, Instrument, Isolate, Media,
    TechnicalReplicate,
)
from aledb_filter.models import AleExperimentFilter
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment


class MutationCountsTestCase(TestCase):

    def setUp(self):
        self.experiment = AleExperiment.objects.create(
            instrument=Instrument.objects.create())
        self.media = Media.objects.create()
        self.freezer_box = FreezerBox.objects.create()
        self.sample = self._sample(ale=1)
        self.other_sample = self._sample(ale=2)

    def _sample(self, ale):
        ale_row = AleId.objects.create(ale_experiment=self.experiment, ale_id=ale)
        flask = Flask.objects.create(ale_id=ale_row, flask_number=100, media=self.media)
        isolate = Isolate.objects.create(flask=flask, isolate_number=1, is_population=False,
                                         freezer_box=self.freezer_box)
        tech_rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        return ResequencingExperiment.objects.create(tech_rep=tech_rep)

    def _mutation(self, mutation_type="SNP", position=100, protein_change="", gene="thrA"):
        return Mutation.objects.create(
            ale_experiment=self.experiment, mutation_type=mutation_type, position=position,
            sequence_change="A>T", protein_change=protein_change, gene=gene)

    def _observe(self, sample, mutation, frequency="1.0000"):
        return ObservedMutation.objects.create(
            sequencing_experiment=sample, mutation=mutation,
            present=True, frequency=frequency)

    def _filter(self, **fields):
        from aledb_filter.util import ensure_default_experiment_filter

        ensure_default_experiment_filter(self.experiment.ale_id)
        AleExperimentFilter.objects.filter(ale_experiment=self.experiment).update(**fields)

    def _counts(self):
        rebuild_mutation_counts()
        return ObservedMutationCounts.objects.all()[0], UniqueMutationCounts.objects.all()[0]

    # ---- observed vs unique ----------------------------------------------------------
    def test_observed_counts_every_observation_and_unique_counts_the_mutation_once(self):
        shared = self._mutation()
        self._observe(self.sample, shared)
        self._observe(self.other_sample, shared)

        observed, unique = self._counts()

        self.assertEqual(2, observed.total)
        self.assertEqual(1, unique.total)
        self.assertEqual(2, observed.single_base_substitution)
        self.assertEqual(1, unique.single_base_substitution)

    # ---- the filter does not reach here ----------------------------------------------
    def test_a_frequency_cutoff_does_not_change_the_totals(self):
        """The clearest case: an observation below an experiment's floor is hidden from every
        table in that experiment and is still something the installation holds."""
        self._observe(self.sample, self._mutation(position=100), frequency="0.0100")
        self._observe(self.sample, self._mutation(position=200))
        self._filter(min_cutoff=50)

        observed, unique = self._counts()

        self.assertEqual(2, observed.total, "the dashboard applied a frequency cutoff")
        self.assertEqual(2, unique.total)

    def test_an_ignored_gene_does_not_change_the_totals(self):
        self._observe(self.sample, self._mutation(position=100, gene="rrlA"))
        self._observe(self.sample, self._mutation(position=200, gene="thrA"))
        self._filter(ignored_genes="rrlA")

        observed, _ = self._counts()

        self.assertEqual(2, observed.total, "the dashboard applied a gene filter")

    def test_a_filter_change_does_not_mark_it_stale(self):
        """The declaration that follows from the above. `mutation_counts` is the most
        expensive rebuild registered, and a filter save marks every experiment at once -- so
        being marked by one would be both wrong and the costliest way to be wrong.
        """
        from aledb_common.rebuild_registry import (
            INPUT_FILTERS, is_stale, request_rebuild, run_rebuilds,
        )

        run_rebuilds(force=True)
        self.assertFalse(is_stale("mutation_counts"))

        request_rebuild(changed=INPUT_FILTERS, reason="test")

        self.assertFalse(is_stale("mutation_counts"),
                         "a filter change marked counts that do not read the filter")

    def test_a_mutation_change_still_marks_it_stale(self):
        """The other half: declaring independence from filters must not make it independent
        of the mutations, which is what it actually counts."""
        from aledb_common.rebuild_registry import (
            INPUT_MUTATIONS, is_stale, request_rebuild, run_rebuilds,
        )

        run_rebuilds(force=True)
        self.assertFalse(is_stale("mutation_counts"))

        request_rebuild(changed=INPUT_MUTATIONS, reason="test")

        self.assertTrue(is_stale("mutation_counts"))

    # ---- the functional-change buckets -----------------------------------------------
    def test_synonymous_and_nonsynonymous_are_written(self):
        """Both columns had sat at zero since they were added: the branch filling them tested
        for `snp_type_synonymous` and `snp_type_nonsynonymous`, which are not the tokens in
        `FUNCTIONAL_CHANGE_TYPE_LIST`."""
        self._observe(self.sample, self._mutation(position=100,
                                                  protein_change="synonymous (L4L)"))
        self._observe(self.sample, self._mutation(position=200,
                                                  protein_change="nonsynonymous (A12T)"))

        observed, unique = self._counts()

        self.assertEqual(1, observed.synonymous)
        self.assertEqual(1, observed.nonsynonymous)
        self.assertEqual(1, unique.synonymous)
        self.assertEqual(1, unique.nonsynonymous)

    def test_a_nonsynonymous_change_is_not_counted_as_synonymous(self):
        """`nonsynonymous` contains `synonymous` as a substring and is listed first in
        `FUNCTIONAL_CHANGE_TYPE_LIST`. The list's order is what keeps these apart, so a tidy-up
        that sorted it would silently move every nonsynonymous mutation into the wrong column.
        """
        self._observe(self.sample, self._mutation(protein_change="nonsynonymous (A12T)"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.nonsynonymous)
        self.assertEqual(0, observed.synonymous)

    def test_one_bucket_per_mutation(self):
        """Unlike the Overview, which counts a mutation under every token its protein_change
        contains. Two pages, two questions; neither is the other's bug."""
        self._observe(self.sample, self._mutation(protein_change="intergenic (-52/+201)"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.intergenic)
        self.assertEqual(1, observed.total)
        self.assertEqual(0, observed.noncoding)

    def test_an_unknown_type_is_bucketed_rather_than_dropped(self):
        """Also unlike the Overview, which drops it from every type count. The totals here
        deliberately exceed the sum of the type columns because UNANNOTATED has no column."""
        self._observe(self.sample, self._mutation(mutation_type="XYZ"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.total)
        self.assertEqual(0, observed.single_base_substitution)

    def test_an_unannotated_mutation_counts_as_unannotated(self):
        self._observe(self.sample, self._mutation(protein_change=""))

        observed, _ = self._counts()

        self.assertEqual(1, observed.unannotated)

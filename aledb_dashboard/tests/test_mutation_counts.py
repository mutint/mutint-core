"""The dashboard's mutation totals, which count the installation rather than a view of it.

`rebuild_mutation_counts` used to run its rows through `filter_observed_mutations`, so the
site-wide totals were computed through whatever frequency cutoffs and ignored-gene lists each
experiment happened to carry. That is a category error twice over:

  * the dashboard is an inventory of what the installation *holds*, and a filter is one
    person's view of one experiment;
  * a filter is becoming per-user, and a shared table cannot be keyed by user -- so a filtered
    site-wide total is not merely wrong, it is not computable.

These tests pin that the filter does not reach here, and that the counts are buckets over every
live observation.

**They also used to be evidence of how a green test can pin the wrong thing.** The
functional-change fixtures built `protein_change="nonsynonymous (A12T)"` -- a format the
annotator has never produced, since `display.py` writes `I34S (ATC->AGC)` -- so the assertions
passed while the real column matched nothing and `synonymous` and `nonsynonymous` sat at zero on
every deployment. They are built on `snp_type` now, which is what the code reads and what breseq
writes.
"""

from django.test import TestCase

from aledb_dashboard.models import ObservedMutationCounts, UniqueMutationCounts
from aledb_dashboard.util import rebuild_mutation_counts
from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Media,
)
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment


class MutationCountsTestCase(TestCase):

    def setUp(self):
        self.experiment = AleExperiment.objects.create(
)
        self.media = Media.objects.create()
        self.sample = self._sample(ale=1)
        self.other_sample = self._sample(ale=2)

    def _sample(self, ale):
        ale_row = AleId.objects.create(ale_experiment=self.experiment, ale_id=ale)
        flask = Flask.objects.create(ale_id=ale_row, flask_number=100, media=self.media)
        return ResequencingExperiment.objects.create(
            flask=flask, isolate_number=1, is_population=False)

    def _mutation(self, mutation_type="SNP", position=100, snp_type="", gene="thrA",
                  protein_change=""):
        """`snp_type` is what decides the bucket. `protein_change` is still a column and still
        the table's "Details" cell, so it stays available to the one test that proves it no
        longer decides anything."""
        return Mutation.objects.create(
            ale_experiment=self.experiment, mutation_type=mutation_type, position=position,
            sequence_change="A>T", snp_type=snp_type, protein_change=protein_change, gene=gene)

    def _observe(self, sample, mutation, frequency="1.0000"):
        return ObservedMutation.objects.create(
            sequencing_experiment=sample, mutation=mutation,
            present=True, frequency=frequency)

    def _filter(self, **fields):
        """The reader's filter, as a value rather than a stored row.

        This used to call `ensure_default_experiment_filter` and then `update()` an
        `AleExperimentFilter`, because an experiment created through the UI had no row until
        something rebuilt one. There is no row: a filter is a value the caller passes in.
        """
        from aledb_filter.view_filter import ViewFilter

        return ViewFilter.parse(min_freq=fields.get("min_cutoff"),
                                max_freq=fields.get("max_cutoff"),
                                genes=fields.get("ignored_genes"))

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

    def test_no_reader_can_change_these_totals(self):
        """Two tests stood here pinning that a filter change did *not* mark these counts stale
        while a mutation change did -- `mutation_counts` declared `inputs={INPUT_MUTATIONS}`
        because it is the most expensive rebuild registered and a filter save marked every
        experiment at once.

        There is no filter save. A filter belongs to whoever is reading and lives in their
        session, so it cannot mark anything for anybody, and the declaration that protected
        against it went with the vocabulary it was written in. What is left to assert is the
        stronger, structural fact: `rebuild_mutation_counts` takes no request, so it has no way
        to reach a reader's filter even if it wanted to.
        """
        import inspect

        from aledb_common.rebuild_registry import is_stale, request_rebuild, run_rebuilds

        self.assertEqual([], list(inspect.signature(rebuild_mutation_counts).parameters))

        run_rebuilds(force=True)
        self.assertFalse(is_stale("mutation_counts"))

        request_rebuild(reason="test")

        self.assertTrue(is_stale("mutation_counts"),
                        "a mutation change must still mark what counts mutations")

    # ---- the functional-change buckets -----------------------------------------------
    def test_synonymous_and_nonsynonymous_are_written(self):
        """Both columns sat at zero on every deployment. The branch filling them tested for
        `snp_type_synonymous` and `snp_type_nonsynonymous`, which are not tokens in the
        vocabulary; and once that was fixed they stayed at zero, because the vocabulary was
        being matched against `protein_change`, which never contains either word."""
        self._observe(self.sample, self._mutation(position=100, snp_type="synonymous"))
        self._observe(self.sample, self._mutation(position=200, snp_type="nonsynonymous"))

        observed, unique = self._counts()

        self.assertEqual(1, observed.synonymous)
        self.assertEqual(1, observed.nonsynonymous)
        self.assertEqual(1, unique.synonymous)
        self.assertEqual(1, unique.nonsynonymous)

    def test_protein_change_no_longer_decides_the_bucket(self):
        """The bug, stated as an assertion. A coding SNP's `protein_change` can contain a word
        from this vocabulary by coincidence -- an intergenic *deletion*'s reads
        `intergenic (-52/+201)` -- and it must not be what counts."""
        self._observe(self.sample, self._mutation(snp_type="nonsynonymous",
                                                  protein_change="intergenic (-52/+201)"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.nonsynonymous)
        self.assertEqual(0, observed.intergenic, "protein_change is deciding the bucket again")

    def test_nonsense_has_its_own_column(self):
        """`nonsense` was missing from the vocabulary entirely, so a stop-codon substitution --
        the most severe thing breseq reports -- was counted as something else."""
        self._observe(self.sample, self._mutation(snp_type="nonsense"))

        observed, unique = self._counts()

        self.assertEqual(1, observed.nonsense)
        self.assertEqual(1, unique.nonsense)

    def test_a_compound_snp_type_takes_the_most_severe_bucket(self):
        """A SNP in two overlapping reading frames carries one value per gene, joined with '|'.
        It is one mutation and must be counted once, under the worse of the two."""
        self._observe(self.sample, self._mutation(snp_type="synonymous|nonsynonymous"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.nonsynonymous)
        self.assertEqual(0, observed.synonymous)
        self.assertEqual(1, observed.total)

    def test_a_non_snp_is_unannotated(self):
        """breseq assigns `snp_type` for SNP and RA entries only, so every DEL, INS, MOB, AMP,
        SUB and INV lands here. That is the deliberate consequence of counting this axis from
        `snp_type`: the breakdown is a SNP breakdown, and a deletion between two genes is no
        longer counted as intergenic the way `protein_change` counted it."""
        self._observe(self.sample, self._mutation(mutation_type="DEL", snp_type="",
                                                  protein_change="intergenic (-52/+201)"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.unannotated)
        self.assertEqual(0, observed.intergenic)

    def test_a_nonsynonymous_change_is_not_counted_as_synonymous(self):
        """`nonsynonymous` contains `synonymous` as a substring. Tokens are split on '|' and
        compared whole now, so this no longer depends on the list's order -- which is free to
        mean severity instead. Kept because the hazard is real and the assertion is cheap."""
        self._observe(self.sample, self._mutation(snp_type="nonsynonymous"))

        observed, _ = self._counts()

        self.assertEqual(1, observed.nonsynonymous)
        self.assertEqual(0, observed.synonymous)

    def test_one_bucket_per_mutation(self):
        """The Overview used to count a mutation under every token it matched, so its sums
        exceeded its mutation count. Both pages resolve to one bucket by severity now, and
        agree."""
        self._observe(self.sample, self._mutation(snp_type="intergenic"))

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

    def test_the_counts_reach_the_page(self):
        """The six functional-change columns were written and rendered nowhere, which is how
        two of them sat at zero unnoticed. A template that silently drops them again should
        fail here rather than on somebody's screen."""
        from django.contrib.auth.models import User

        self._observe(self.sample, self._mutation(snp_type="nonsense"))
        self._observe(self.sample, self._mutation(position=200, snp_type="nonsynonymous"))
        rebuild_mutation_counts()

        user = User.objects.create(username="viewer", email="v@e.com", is_active=True)
        self.client.force_login(user)
        html = self.client.get("/dashboard", follow=True).content.decode()

        self.assertIn("Functional Change Counts", html)
        self.assertIn("Nonsense", html)
        self.assertIn("Nonsynonymous", html)

    def test_an_unannotated_mutation_counts_as_unannotated(self):
        """Empty, the bare separator (a non-SNP in two overlapping genes: the join is
        unconditional) and NULL (rows imported before the annotator existed) all mean the same
        thing here."""
        for index, snp_type in enumerate(("", "|", None)):
            with self.subTest(snp_type=snp_type):
                mutation = self._mutation(position=1000 + index, snp_type=snp_type)
                self._observe(self.sample, mutation)

        observed, _ = self._counts()

        self.assertEqual(3, observed.unannotated)

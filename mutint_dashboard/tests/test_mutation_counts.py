"""The dashboard's mutation totals, which count the installation rather than a view of it.

`rebuild_mutation_counts` used to run its rows through `filter_mutation_calls`, so the
site-wide totals were computed through whatever frequency cutoffs and ignored-gene lists each
experiment happened to carry. That is a category error twice over:

  * the dashboard is an inventory of what the installation *holds*, and a filter is one
    person's view of one experiment;
  * a filter is becoming per-user, and a shared table cannot be keyed by user -- so a filtered
    site-wide total is not merely wrong, it is not computable.

These tests pin that the filter does not reach here, and that the counts are buckets over every
live call.

**They also used to be evidence of how a green test can pin the wrong thing.** The
functional-change fixtures built `protein_change="nonsynonymous (A12T)"` -- a format the
annotator has never produced, since `display.py` writes `I34S (ATC->AGC)` -- so the assertions
passed while the real column matched nothing and `synonymous` and `nonsynonymous` sat at zero on
every deployment. They are built on `snp_type` now, which is what the code reads and what breseq
writes.
"""

from django.test import TestCase

from mutint_dashboard.models import InstallationCounts
from mutint_dashboard.util import counts
from mutint_dashboard.util import rebuild_mutation_counts
from mutint_experiment.models import (
    Experiment, Population,
)
from mutint_sample.models import Mutation, MutationCall, Sample


#: Column spelling -> where that number now lives. Only the type half needs a map: the
#: functional-change tokens were always spelled identically to their columns, which is the
#: equality `mutint_dashboard.util` used to rely on.
_TYPE_COLUMNS = {
    "single_base_substitution": "SNP",
    "multiple_base_substitution": "SUB",
    "deletion": "DEL",
    "insertion": "INS",
    "mobile_element_insertion": "MOB",
    "amplification": "AMP",
    "gene_conversion": "CON",
    "inversion": "INV",
}


class _Counts:
    """Attribute access over one stored payload, so the assertions did not have to move."""

    def __init__(self, data):
        self._data = data

    @property
    def total(self):
        return self._data.get("total", 0)

    def __getattr__(self, name):
        token = _TYPE_COLUMNS.get(name)
        if token is not None:
            return (self._data.get("type") or {}).get(token, 0)
        return (self._data.get("functional_change") or {}).get(name, 0)


class MutationCountsTestCase(TestCase):

    def setUp(self):
        self.experiment = Experiment.objects.create(
)
        self.sample = self._sample(ale=1)
        self.other_sample = self._sample(ale=2)

    def _sample(self, ale):
        ale_row = Population.objects.create(experiment=self.experiment, name=ale)
        return Sample.objects.create(
            population=ale_row, time_point=100, name=1, is_clonal=True)

    def _mutation(self, mutation_type="SNP", position=100, snp_type="", gene="thrA",
                  protein_change=""):
        """`snp_type` is what decides the bucket. `protein_change` is still a column and still
        the table's "Details" cell, so it stays available to the one test that proves it no
        longer decides anything."""
        return Mutation.objects.create(
            experiment=self.experiment, mutation_type=mutation_type, start_position=position,
            sequence_change="A>T", snp_type=snp_type, protein_change=protein_change, gene=gene)

    def _observe(self, sample, mutation, frequency="1.0000"):
        return MutationCall.objects.create(
            sample=sample, mutation=mutation,
            present=True, frequency=frequency)

    def _filter(self, **fields):
        """The reader's filter, as a value rather than a stored row.

        This used to call `ensure_default_experiment_filter` and then `update()` an
        `AleExperimentFilter`, because an experiment created through the UI had no row until
        something rebuilt one. There is no row: a filter is a value the caller passes in.
        """
        from mutint_filter.view_filter import ViewFilter

        return ViewFilter.parse(min_freq=fields.get("min_cutoff"),
                                max_freq=fields.get("max_cutoff"),
                                genes=fields.get("ignored_genes"))

    def _counts(self):
        """The two payloads, wrapped so the assertions below read as they did against columns.

        `_Counts` is what keeps this module's ~90 assertions unchanged through the collapse:
        the numbers and their names are the same, only their home moved. It resolves a token
        against both sub-dicts because a caller here asks for `single_base_substitution` or
        `nonsense` without caring which vocabulary it came from -- the two are kept apart in
        storage, where the collision would matter, not in a test helper.
        """
        rebuild_mutation_counts()
        return (_Counts(counts(InstallationCounts.MUTATION_CALLS)),
                _Counts(counts(InstallationCounts.UNIQUE_MUTATIONS)))

    # ---- observed vs unique ----------------------------------------------------------
    def test_observed_counts_every_call_and_unique_counts_the_mutation_once(self):
        shared = self._mutation()
        self._observe(self.sample, shared)
        self._observe(self.other_sample, shared)

        calls, unique = self._counts()

        self.assertEqual(2, calls.total)
        self.assertEqual(1, unique.total)
        self.assertEqual(2, calls.single_base_substitution)
        self.assertEqual(1, unique.single_base_substitution)

    # ---- the filter does not reach here ----------------------------------------------
    def test_a_frequency_cutoff_does_not_change_the_totals(self):
        """The clearest case: a call below an experiment's floor is hidden from every
        table in that experiment and is still something the installation holds."""
        self._observe(self.sample, self._mutation(position=100), frequency="0.0100")
        self._observe(self.sample, self._mutation(position=200))
        self._filter(min_cutoff=50)

        calls, unique = self._counts()

        self.assertEqual(2, calls.total, "the dashboard applied a frequency cutoff")
        self.assertEqual(2, unique.total)

    def test_an_ignored_gene_does_not_change_the_totals(self):
        self._observe(self.sample, self._mutation(position=100, gene="rrlA"))
        self._observe(self.sample, self._mutation(position=200, gene="thrA"))
        self._filter(ignored_genes="rrlA")

        calls, _ = self._counts()

        self.assertEqual(2, calls.total, "the dashboard applied a gene filter")

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

        from mutint_common.rebuild_registry import is_stale, request_rebuild, run_rebuilds

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

        calls, unique = self._counts()

        self.assertEqual(1, calls.synonymous)
        self.assertEqual(1, calls.nonsynonymous)
        self.assertEqual(1, unique.synonymous)
        self.assertEqual(1, unique.nonsynonymous)

    def test_protein_change_no_longer_decides_the_bucket(self):
        """The bug, stated as an assertion. A coding SNP's `protein_change` can contain a word
        from this vocabulary by coincidence -- an intergenic *deletion*'s reads
        `intergenic (-52/+201)` -- and it must not be what counts."""
        self._observe(self.sample, self._mutation(snp_type="nonsynonymous",
                                                  protein_change="intergenic (-52/+201)"))

        calls, _ = self._counts()

        self.assertEqual(1, calls.nonsynonymous)
        self.assertEqual(0, calls.intergenic, "protein_change is deciding the bucket again")

    def test_nonsense_has_its_own_column(self):
        """`nonsense` was missing from the vocabulary entirely, so a stop-codon substitution --
        the most severe thing breseq reports -- was counted as something else."""
        self._observe(self.sample, self._mutation(snp_type="nonsense"))

        calls, unique = self._counts()

        self.assertEqual(1, calls.nonsense)
        self.assertEqual(1, unique.nonsense)

    def test_a_compound_snp_type_takes_the_most_severe_bucket(self):
        """A SNP in two overlapping reading frames carries one value per gene, joined with '|'.
        It is one mutation and must be counted once, under the worse of the two."""
        self._observe(self.sample, self._mutation(snp_type="synonymous|nonsynonymous"))

        calls, _ = self._counts()

        self.assertEqual(1, calls.nonsynonymous)
        self.assertEqual(0, calls.synonymous)
        self.assertEqual(1, calls.total)

    def test_a_non_snp_is_unannotated(self):
        """breseq assigns `snp_type` for SNP and RA entries only, so every DEL, INS, MOB, AMP,
        SUB and INV lands here. That is the deliberate consequence of counting this axis from
        `snp_type`: the breakdown is a SNP breakdown, and a deletion between two genes is no
        longer counted as intergenic the way `protein_change` counted it."""
        self._observe(self.sample, self._mutation(mutation_type="DEL", snp_type="",
                                                  protein_change="intergenic (-52/+201)"))

        calls, _ = self._counts()

        self.assertEqual(1, calls.unannotated)
        self.assertEqual(0, calls.intergenic)

    def test_a_nonsynonymous_change_is_not_counted_as_synonymous(self):
        """`nonsynonymous` contains `synonymous` as a substring. Tokens are split on '|' and
        compared whole now, so this no longer depends on the list's order -- which is free to
        mean severity instead. Kept because the hazard is real and the assertion is cheap."""
        self._observe(self.sample, self._mutation(snp_type="nonsynonymous"))

        calls, _ = self._counts()

        self.assertEqual(1, calls.nonsynonymous)
        self.assertEqual(0, calls.synonymous)

    def test_one_bucket_per_mutation(self):
        """The Overview used to count a mutation under every token it matched, so its sums
        exceeded its mutation count. Both pages resolve to one bucket by severity now, and
        agree."""
        self._observe(self.sample, self._mutation(snp_type="intergenic"))

        calls, _ = self._counts()

        self.assertEqual(1, calls.intergenic)
        self.assertEqual(1, calls.total)
        self.assertEqual(0, calls.noncoding)

    def test_an_unknown_type_is_bucketed_rather_than_dropped(self):
        """Also unlike the Overview, which drops it from every type count. The totals here
        deliberately exceed the sum of the type columns because UNANNOTATED has no column."""
        self._observe(self.sample, self._mutation(mutation_type="XYZ"))

        calls, _ = self._counts()

        self.assertEqual(1, calls.total)
        self.assertEqual(0, calls.single_base_substitution)

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

    def test_every_bucket_in_both_vocabularies_reaches_the_page(self):
        """The guard the previous test was reaching for, generalised.

        That one names two labels, so fifteen of the seventeen buckets could stop rendering
        without it noticing -- and they render through a `{% for %}` over a vocabulary now, so
        a token the view cannot resolve produces a row with an empty cell rather than an
        error. Django swallows both. Driving the assertion off the same two lists the page is
        built from is what makes adding a token safe: it is counted, shown, and checked here,
        with no migration and no third place to remember.
        """
        from django.contrib.auth.models import User

        from mutint_sample.functional_change import (
            FUNCTIONAL_CHANGE_LABELS, FUNCTIONAL_CHANGE_TYPE_LIST,
        )
        from mutint_sample.views.common import MUTATION_TYPE_LABELS, MUTATION_TYPE_LIST

        import re

        rebuild_mutation_counts()
        user = User.objects.create(username="every", email="e@e.com", is_active=True)
        self.client.force_login(user)
        response = self.client.get("/dashboard", follow=True)
        html = response.content.decode()

        # Both halves, because either alone passes while the page is wrong. The context check
        # catches a bucket the *view* drops; the row count catches a template that stops
        # looping. And `assertIn(label, html)` on its own catches neither of them for
        # `unannotated`, which is a token in **both** vocabularies -- so the functional-change
        # table goes on rendering "Unannotated" after the type table has stopped. That is the
        # exact bucket the retired `if/elif` chain discarded, so the obvious spelling of this
        # test is blind to the one bug it exists for.
        for key, vocabulary, labels in (
                ("type_rows", MUTATION_TYPE_LIST, MUTATION_TYPE_LABELS),
                ("change_rows", FUNCTIONAL_CHANGE_TYPE_LIST, FUNCTIONAL_CHANGE_LABELS)):
            with self.subTest(table=key):
                self.assertEqual([labels[token] for token in vocabulary],
                                 [label for label, _total, _unique in response.context[key]])

        bodies = re.findall(r"<tbody>(.*?)</tbody>", html, re.S)
        self.assertEqual(3, len(bodies), "the dashboard's three tables")
        self.assertEqual(len(MUTATION_TYPE_LIST), bodies[1].count("<tr>"))
        self.assertEqual(len(FUNCTIONAL_CHANGE_TYPE_LIST), bodies[2].count("<tr>"))

    def test_the_type_counts_add_up_to_the_total(self):
        """Newly true, and the point of storing the buckets by vocabulary token.

        `MUTATION_TYPE_LIST` has nine entries and the `if/elif` chain that wrote these columns
        had eight branches, so the `unannotated` bucket was counted and dropped -- a comment
        in `util.py` recorded that the displayed types therefore did not sum to the total.
        Nothing rendered the difference, so nothing could notice it.
        """
        self._observe(self.sample, self._mutation(snp_type="nonsense"))
        self._observe(self.sample, self._mutation(position=200, mutation_type="XYZ"))
        calls, unique = self._counts()

        for row in (calls, unique):
            with self.subTest(row=row):
                buckets = row._data["type"]
                self.assertEqual(row.total, sum(buckets.values()))
                self.assertTrue(buckets["unannotated"],
                                "the unannotated bucket is what used to be discarded")

    def test_an_unannotated_mutation_counts_as_unannotated(self):
        """Empty, the bare separator (a non-SNP in two overlapping genes: the join is
        unconditional) and NULL (rows imported before the annotator existed) all mean the same
        thing here."""
        for index, snp_type in enumerate(("", "|", None)):
            with self.subTest(snp_type=snp_type):
                mutation = self._mutation(position=1000 + index, snp_type=snp_type)
                self._observe(self.sample, mutation)

        calls, _ = self._counts()

        self.assertEqual(3, calls.unannotated)

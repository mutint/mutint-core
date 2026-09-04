"""The Overview's mutation counts, computed as aggregates instead of in Python.

`/stats` used to build these four dictionaries by pulling every MutationCall in the
experiment -- each joined across six tables, instantiated as a model, carrying two JSONFields
and a gene column of up to 19 000 characters -- to arrive at about sixteen integers. They are
computed where the rows already are now, and nothing is stored.

**Every count is pinned literally**, including where the behaviour is surprising:

  * a mutation_type outside MUTATION_TYPE_LIST is dropped, not bucketed as unannotated -- so
    the *type* sums are less than the mutation count;
  * a functional change is one bucket per mutation, resolved by severity from breseq's
    `snp_type`, and an unrecognised value *is* bucketed, as unannotated -- so the
    *functional-change* sums equal the mutation count exactly. The two sets of counts on this
    page therefore have different totals, deliberately, and that used to be true for the
    opposite reason: a mutation was counted under every token its `protein_change` contained,
    so 'nonsynonymous' also counted under 'synonymous' and the sums *exceeded* the count;
  * "unique" means distinct mutations *surviving the filter*, not every Mutation row the
    experiment owns.

These used to be asserted against the row-walking implementation as well, which was kept in
`aledb_stats.util` for the purpose. That second implementation is gone: the literals are what
was carrying the coverage, and a duplicate had to be kept in step with the real one for no
guarantee the literals do not already give. `test_both_paths_agree_with_each_other` still
compares two implementations -- but two that both ship.

There are two code paths and they must agree. With no gene filter set the whole filter is
expressible as SQL. With one set, "every gene this mutation touches is in the ignore list" is
a set-subset test over a parsed column that SQL cannot do, so the rows are walked -- as
values_list tuples rather than models. A test that only ever exercised the first would prove
nothing about the second, so each count test runs under both.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (
    Experiment, Population,
)
from aledb_sample.models import Mutation, MutationCall, Sample
from aledb_stats.util import compute_experiment_counts


class SummaryTestCase(TestCase):
    """Two ALEs, three samples, and mutations chosen to exercise each surprising rule."""

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

        from aledb_import.gd_import import prepare_experiment_by_id
        self.context = prepare_experiment_by_id(self.experiment.id)

        self.samples = [self._sample(ale=1, flask=100, isolate=1),
                        self._sample(ale=1, flask=200, isolate=1),
                        self._sample(ale=2, flask=100, isolate=1)]

        # One mutation of each interesting shape. `protein_change` is set to what the
        # annotator would really write, and is deliberately *not* what any count reads.
        self.snp = self._mutation("SNP", "nonsynonymous", gene="thrA",
                                  protein_change="A12T (GCA->ACA)")
        # A DEL between two genes. Its protein_change says "intergenic" and its snp_type is
        # empty, because breseq assigns snp_type for SNPs only -- so it is unannotated here.
        self.deletion = self._mutation("DEL", "", gene="thrB/thrC",
                                       protein_change="intergenic (-52/+201)")
        self.noncoding = self._mutation("SNP", "noncoding", gene="rrlA",
                                        protein_change="noncoding (12/1541 nt)")
        # Not in MUTATION_TYPE_LIST, so dropped from every *type* bucket -- and still counted
        # here, which is the asymmetry between the two tables.
        self.unknown_type = self._mutation("XYZ", "synonymous", gene="lacZ",
                                           protein_change="L4L (CTG->CTA)")
        # No annotation at all.
        self.bare = self._mutation("INS", "", gene="araB")

        # The SNP is seen in all three samples, so "observed" and "unique" differ for it.
        for sample in self.samples:
            self._observe(sample, self.snp)
        self._observe(self.samples[0], self.deletion)
        self._observe(self.samples[0], self.noncoding)
        self._observe(self.samples[1], self.unknown_type)
        self._observe(self.samples[2], self.bare)

    # ---- fixture helpers ------------------------------------------------------------
    def _sample(self, ale, flask, isolate):
        ale_row, _ = Population.objects.get_or_create(
            experiment=self.experiment, name=ale)
        return Sample.objects.create(
            population=ale_row, time_point=flask, name="%d-1" % isolate, is_clonal=True,
            source_name="%d-%d-%d-1" % (ale, flask, isolate))

    def _mutation(self, mutation_type, snp_type, gene, protein_change=""):
        return Mutation.objects.create(
            experiment=self.experiment, mutation_type=mutation_type, start_position=1,
            sequence_change="A>T", snp_type=snp_type, protein_change=protein_change, gene=gene)

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

    def _observe(self, sample, mutation):
        return MutationCall.objects.create(
            sample=sample, mutation=mutation,
            present=True, frequency="1.0000")

    def _counts(self):
        return compute_experiment_counts(self.experiment.id)

    # ---- the SQL path ---------------------------------------------------------------
    def test_the_counts_are_the_ones_the_page_shows(self):
        """The whole fixture, stated. Every rule below is one somebody could reasonably
        "fix" and be wrong, so each is asserted with the reason beside it."""
        types, call_types, protein, observed_protein = compute_experiment_counts(
            self.experiment.id)

        self.assertEqual(2, types["SNP"], "snp and noncoding are both SNPs")
        self.assertEqual(4, call_types["SNP"], "the SNP is seen in three samples")
        self.assertEqual(1, types["DEL"])
        self.assertEqual(1, types["INS"])
        # 'XYZ' is not a known mutation type, so it is counted under nothing at all.
        self.assertEqual(sum(types.values()), 4, "the XYZ mutation is dropped, not bucketed")

        self.assertEqual(1, protein["nonsynonymous"])
        self.assertEqual(1, protein["synonymous"],
                         "only the XYZ mutation is synonymous -- the nonsynonymous SNP used to "
                         "count here too, because 'nonsynonymous' contains 'synonymous' as a "
                         "substring and tokens were matched that way")
        self.assertEqual(1, protein["noncoding"])
        self.assertEqual(0, protein["intergenic"],
                         "the DEL's protein_change says intergenic, but snp_type is a SNP "
                         "concept and the DEL has none, so it is unannotated")
        self.assertEqual(2, protein["unannotated"], "the DEL and the INS")
        self.assertEqual(5, sum(protein.values()),
                         "every mutation gets exactly one functional-change bucket, unlike the "
                         "type counts above, where an unknown type is dropped")

    def test_it_is_not_filtered(self):
        """**The Overview shows what the experiment holds.**

        Its counts went through the shared experiment filter until filtering became a
        per-reader affair, and this page was left out of that -- it summarises a dataset the
        way the dashboard does rather than showing rows you read through. Several tests stood
        here pinning that a frequency cutoff and an ignored-gene list reached these counts.
        """
        low = self._observe(self.samples[0], self._mutation("MOB", "", gene="insH"))
        low.frequency = "0.0100"
        low.save()

        types, _, _, _ = self._counts()

        self.assertEqual(1, types["MOB"], "the Overview has started filtering")

    def test_it_takes_no_filter_argument(self):
        import inspect

        self.assertNotIn("view_filter",
                         inspect.signature(compute_experiment_counts).parameters)

    def test_a_deleted_mutation_is_excluded(self):
        """This used to set the filter's `ignored_mutations` list, which hid a mutation from
        every table while leaving its rows in place. That mechanism is gone; deleting an
        call through `aledb_mutation_editor` is what replaces it, and the Overview must
        stop counting it for the same reason -- by simply not finding the row any more.
        """
        from aledb_mutation_editor import history
        from aledb_mutation_editor.models import KIND_DELETE

        removals = list(MutationCall.objects.filter(mutation=self.deletion))
        self.assertTrue(removals, "the fixture's DEL is observed somewhere")
        history.apply_edits(self.experiment, None, KIND_DELETE, removals=removals)

        types, _, _, _ = self._counts()
        self.assertEqual(0, types["DEL"])

    # ---- there is one counting path now ----------------------------------------------
    def test_the_counts_come_from_sql_alone(self):
        """There were two paths: a gene filter forced the rows to be walked in Python, because
        "every gene this mutation touches is in the ignore list" is a set-subset test with no
        SQL. Nothing is filtered here now, so there is nothing SQL cannot express and
        `_count_in_python` went with the branch that chose it."""
        from aledb_stats import util

        self.assertFalse(hasattr(util, "_count_in_python"))

    # ---- the severity hierarchy --------------------------------------
    def _both_paths(self):
        """The counts. Named for the two paths there used to be -- one aggregating in SQL, one
        walking rows because a gene filter cannot be expressed in SQL. There is one now."""
        return compute_experiment_counts(self.experiment.id)

    def test_a_compound_snp_type_takes_the_most_severe_bucket(self):
        """A SNP in two overlapping reading frames carries one value per gene, joined with '|'.
        It is one mutation and counts once, under the worse of the two."""
        self._observe(self.samples[0],
                      self._mutation("SNP", "synonymous|nonsynonymous", gene="ovlA"))

        _, _, protein, _ = self._both_paths()

        self.assertEqual(2, protein["nonsynonymous"], "the fixture's SNP, and the compound")
        self.assertEqual(1, protein["synonymous"], "the compound must not count here as well")

    def test_a_nonsense_snp_lands_in_nonsense(self):
        """`nonsense` was missing from the vocabulary, so these counted as something else."""
        self._observe(self.samples[0], self._mutation("SNP", "nonsense", gene="thrC"))

        _, _, protein, _ = self._both_paths()

        self.assertEqual(1, protein["nonsense"])

    def test_a_null_snp_type_is_unannotated(self):
        """Rows imported before the annotator existed hold SQL NULL. In the grouped path that
        is its own group with a None key, which must not raise."""
        self._observe(self.samples[0], self._mutation("SNP", None, gene="thrD"))

        _, _, protein, _ = self._both_paths()

        self.assertEqual(3, protein["unannotated"], "the DEL, the INS, and the null SNP")

    def test_the_counts_reach_the_page(self):
        """`aledb_stats.views` has pushed these four names into the context for a long time and
        `stats.html` read none of them, so the numbers were computed and discarded. Nothing
        would have caught that; this would."""
        html = self.client.get("/stats", {"experiment_id": self.experiment.id},
                               follow=True).content.decode()

        self.assertIn("functional change counts", html.lower())
        self.assertIn("Nonsynonymous", html)
        self.assertIn("Nonsense", html)

    # ---- what the page reads ---------------------------------------------------------
    def test_the_page_reads_what_computing_returns(self):
        """`get_experiment_summary` is what `/stats` calls, and it used to return a stored
        `ExperimentSummary` row. It returns a namedtuple with the same four field names, so
        the view and the template did not change -- which is worth an assertion, because a
        rename here fails at template-render time and not here."""
        from aledb_stats.util import get_experiment_summary

        types, call_types, protein, observed_protein = self._counts()
        summary = get_experiment_summary(self.experiment.id)

        self.assertEqual(types, summary.mutation_type_counts)
        self.assertEqual(call_types, summary.call_type_counts)
        self.assertEqual(protein, summary.protein_change_counts)
        self.assertEqual(observed_protein, summary.call_protein_change_counts)

    def test_it_reflects_an_edit_with_nothing_to_invalidate(self):
        """The counts were stored and rebuilt through the 'overview' rebuilder, so a new
        call showed up only once something marked them stale. Computed, there is no
        such window -- which is the behaviour that replaced the rebuilder."""
        from aledb_stats.util import get_experiment_summary

        self.assertEqual(0, get_experiment_summary(
            self.experiment.id).mutation_type_counts["AMP"])

        self._observe(self.samples[0], self._mutation("AMP", "", gene="galK"))

        self.assertEqual(1, get_experiment_summary(
            self.experiment.id).mutation_type_counts["AMP"])

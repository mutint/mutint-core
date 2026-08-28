"""The Overview's mutation counts, computed as aggregates instead of in Python.

`/stats` used to build these four dictionaries by pulling every ObservedMutation in the
experiment -- each joined across six tables, instantiated as a model, carrying two JSONFields
and a gene column of up to 19 000 characters -- to arrive at about sixteen integers. They are
computed where the rows already are now, and nothing is stored.

**Every count is pinned literally**, including where the behaviour is surprising:

  * a mutation_type outside MUTATION_TYPE_LIST is dropped, not bucketed as unannotated;
  * a protein_change counts once per functional-change token it contains, so 'nonsynonymous'
    also counts under 'synonymous' and those sums exceed the mutation count;
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
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_filter.models import AleExperimentFilter
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment
from aledb_stats.util import compute_experiment_counts


class SummaryTestCase(TestCase):
    """Two ALEs, three samples, and mutations chosen to exercise each surprising rule."""

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        from aledb_import.gd_import import prepare_experiment_by_id
        self.context = prepare_experiment_by_id(self.experiment.ale_id)

        self.samples = [self._sample(ale=1, flask=100, isolate=1),
                        self._sample(ale=1, flask=200, isolate=1),
                        self._sample(ale=2, flask=100, isolate=1)]

        # One mutation of each interesting shape.
        self.snp = self._mutation("SNP", "nonsynonymous (A12T)", gene="thrA")
        self.deletion = self._mutation("DEL", "intergenic (-52/+201)", gene="thrB/thrC")
        self.noncoding = self._mutation("SNP", "noncoding (12/1541 nt)", gene="rrlA")
        # Not in MUTATION_TYPE_LIST -- the old helpers drop it rather than bucketing it.
        self.unknown_type = self._mutation("XYZ", "synonymous (L4L)", gene="lacZ")
        # No annotation at all: counts under a mutation type and under no protein change.
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
        ale_row, _ = AleId.objects.get_or_create(
            ale_experiment=self.experiment, ale_id=ale)
        flask_row, _ = Flask.objects.get_or_create(
            ale_id=ale_row, flask_number=flask,
            defaults={"media": self.context["media"]})
        isolate_row = Isolate.objects.create(
            flask=flask_row, isolate_number=isolate, is_population=False,
            freezer_box=self.context["freezer_box"])
        tech_rep = TechnicalReplicate.objects.create(isolate=isolate_row, tech_rep_number=1)
        return ResequencingExperiment.objects.create(
            tech_rep=tech_rep, sample_name="%d-%d-%d-1" % (ale, flask, isolate))

    def _mutation(self, mutation_type, protein_change, gene):
        return Mutation.objects.create(
            ale_experiment=self.experiment, mutation_type=mutation_type, position=1,
            sequence_change="A>T", protein_change=protein_change, gene=gene)

    def _filter(self, **fields):
        """Set fields on this experiment's filter, creating the row if it has none.

        An experiment created through the UI has no AleExperimentFilter until something
        rebuilds it -- `ensure_default_experiment_filter` is the registered rebuild that now
        does, and it used to be the first statement of `gd_import.run_post_processing`, where
        only an import could reach it. A bare `.update()` here would silently match no rows.
        """
        from aledb_filter.util import ensure_default_experiment_filter

        ensure_default_experiment_filter(self.experiment.ale_id)
        AleExperimentFilter.objects.filter(ale_experiment=self.experiment).update(**fields)

    def _observe(self, sample, mutation):
        return ObservedMutation.objects.create(
            sequencing_experiment=sample, mutation=mutation,
            present=True, frequency="1.0000")

    def _counts(self):
        return compute_experiment_counts(self.experiment.ale_id)

    # ---- the SQL path ---------------------------------------------------------------
    def test_the_counts_are_the_ones_the_page_shows(self):
        """The whole fixture, stated. Every rule below is one somebody could reasonably
        "fix" and be wrong, so each is asserted with the reason beside it."""
        types, observed_types, protein, observed_protein = compute_experiment_counts(
            self.experiment.ale_id)

        self.assertEqual(2, types["SNP"], "snp and noncoding are both SNPs")
        self.assertEqual(4, observed_types["SNP"], "the SNP is seen in three samples")
        self.assertEqual(1, types["DEL"])
        self.assertEqual(1, types["INS"])
        # 'XYZ' is not a known mutation type, so it is counted under nothing at all.
        self.assertEqual(sum(types.values()), 4, "the XYZ mutation is dropped, not bucketed")

        self.assertEqual(1, protein["nonsynonymous"])
        self.assertEqual(2, protein["synonymous"],
                         "the SNP's 'nonsynonymous' contains 'synonymous' and so counts "
                         "under both, and the XYZ mutation's is 'synonymous' outright -- "
                         "a mutation dropped from every *type* bucket still counts here")
        self.assertEqual(1, protein["intergenic"])
        self.assertEqual(1, protein["noncoding"])

    def test_a_frequency_cutoff_is_applied(self):
        """Excluded in SQL, by the queryset both paths share.

        This used to need `frequency_gatk` set alongside `frequency` or it failed, and said
        so: the filter ANDed a `frequency_gatk__lt` term in whenever `min_gatk_cutoff` was
        set, which was always, and a comparison against null is never true -- so a real
        observation, which never had a GATK frequency, ignored the cutoff entirely. The
        column is gone and the workaround with it, so this now pins the cutoff against the
        rows an import actually produces.
        """
        low = self._observe(self.samples[0], self._mutation("MOB", "", gene="insH"))
        low.frequency = "0.0100"
        low.save()
        self._filter(min_cutoff=50)

        types, _, _, _ = self._counts()
        self.assertEqual(0, types["MOB"], "the 1% observation is below the 50% cutoff")

    def test_a_deleted_mutation_is_excluded(self):
        """This used to set the filter's `ignored_mutations` list, which hid a mutation from
        every table while leaving its rows in place. That mechanism is gone; deleting an
        observation through `aledb_mutation_editor` is what replaces it, and the Overview must
        stop counting it for the same reason -- by simply not finding the row any more.
        """
        from aledb_mutation_editor import history
        from aledb_mutation_editor.models import KIND_DELETE

        removals = list(ObservedMutation.objects.filter(mutation=self.deletion))
        self.assertTrue(removals, "the fixture's DEL is observed somewhere")
        history.apply_changes(self.experiment, None, KIND_DELETE, removals=removals)

        types, _, _, _ = self._counts()
        self.assertEqual(0, types["DEL"])

    # ---- the Python path ------------------------------------------------------------
    def test_a_noncoding_gene_filter_matches_the_original(self):
        """A gene filter forces the row-walking path; it must reach the same answer.

        This case used the site-wide list before it was removed. There is one filter now, so
        it exercises the same path through the experiment's own."""
        self._filter(ignored_genes="rrlA")
        types, _, _, _ = self._counts()
        self.assertEqual(1, types["SNP"],
                         "the noncoding SNP is in the ignored gene; the other SNP is not")

    def test_an_experiment_gene_filter_matches_the_original(self):
        self._filter(ignored_genes="araB")
        types, _, _, _ = self._counts()
        self.assertEqual(0, types["INS"], "araB was the only INS")

    def test_a_multi_gene_mutation_survives_a_partial_gene_filter(self):
        """The rule is subset, not intersection: ignoring one of two genes ignores nothing.

        This is the branch that cannot be expressed in SQL and the reason the second path
        exists at all, so it is the one worth pinning.
        """
        self._filter(ignored_genes="thrB")
        types, _, _, _ = self._counts()
        self.assertEqual(1, types["DEL"], "thrB/thrC is not a subset of {thrB}")

    def test_both_paths_agree_with_each_other(self):
        """Same data, same answer, whichever branch computes it."""
        with_sql = compute_experiment_counts(self.experiment.ale_id)
        # A gene filter naming a gene no mutation has changes no count, but does force the
        # row-walking path -- which is precisely how to compare the two on one dataset.
        self._filter(ignored_genes="notAGene")
        with_python = compute_experiment_counts(self.experiment.ale_id)
        self.assertEqual(with_sql, with_python)

    # ---- what the page reads ---------------------------------------------------------
    def test_the_page_reads_what_computing_returns(self):
        """`get_experiment_summary` is what `/stats` calls, and it used to return a stored
        `ExperimentSummary` row. It returns a namedtuple with the same four field names, so
        the view and the template did not change -- which is worth an assertion, because a
        rename here fails at template-render time and not here."""
        from aledb_stats.util import get_experiment_summary

        types, observed_types, protein, observed_protein = self._counts()
        summary = get_experiment_summary(self.experiment.ale_id)

        self.assertEqual(types, summary.mutation_type_counts)
        self.assertEqual(observed_types, summary.observed_mutation_type_counts)
        self.assertEqual(protein, summary.protein_change_counts)
        self.assertEqual(observed_protein, summary.observed_protein_change_counts)

    def test_it_reflects_an_edit_with_nothing_to_invalidate(self):
        """The counts were stored and rebuilt through the 'overview' rebuilder, so a new
        observation showed up only once something marked them stale. Computed, there is no
        such window -- which is the behaviour that replaced the rebuilder."""
        from aledb_stats.util import get_experiment_summary

        self.assertEqual(0, get_experiment_summary(
            self.experiment.ale_id).mutation_type_counts["AMP"])

        self._observe(self.samples[0], self._mutation("AMP", "", gene="galK"))

        self.assertEqual(1, get_experiment_summary(
            self.experiment.ale_id).mutation_type_counts["AMP"])

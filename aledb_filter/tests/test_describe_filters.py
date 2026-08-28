"""What a page says about its own filtering, and why it is derived rather than written.

`describe_filters` and `filtered_observed_mutation_queryset` both read `filters_in_play`, so
the sentence a table shows and the exclusion it applied come from one resolution. Deriving the
description separately would be a second opinion about which filters apply, and a page
confidently describing filtering it is not doing is worse than a page saying nothing.

The last test here is the one that earns that arrangement: it asserts the two agree on the same
fixture, and it is what fails if they ever drift.
"""

from decimal import Decimal

from django.test import TestCase

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, FreezerBox, Instrument, Isolate, Media,
    TechnicalReplicate,
)
from aledb_filter.models import AleExperimentFilter
from aledb_filter.util import describe_filters, filter_observed_mutations
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment


class DescribeFiltersTestCase(TestCase):

    def setUp(self):
        self.media = Media.objects.create()
        self.freezer_box = FreezerBox.objects.create()
        self.experiment = AleExperiment.objects.create(
            instrument=Instrument.objects.create())
        ale = AleId.objects.create(ale_experiment=self.experiment, ale_id=1)
        flask = Flask.objects.create(ale_id=ale, flask_number=1, media=self.media)
        isolate = Isolate.objects.create(flask=flask, isolate_number=1,
                                         is_population=False,
                                         freezer_box=self.freezer_box)
        replicate = TechnicalReplicate.objects.create(isolate=isolate)
        self.sample = ResequencingExperiment.objects.create(tech_rep=replicate)

    def _filter(self, **fields):
        fields.setdefault("min_cutoff", 0)
        fields.setdefault("max_cutoff", 100)
        return AleExperimentFilter.objects.update_or_create(
            ale_experiment=self.experiment, defaults=fields)[0]

    def _observe(self, frequency, gene="thrA", position=100):
        mutation = Mutation.objects.create(
            ale_experiment=self.experiment, mutation_type="SNP", position=position,
            reseq_reference="NC_000913", sequence_change="A>T", gene=gene)
        return ObservedMutation.objects.create(
            sequencing_experiment=self.sample, mutation=mutation, present=True,
            frequency=Decimal(frequency))

    def describe(self, **kwargs):
        return describe_filters(experiment_id=self.experiment.ale_id, **kwargs)

    # --- what it reports ------------------------------------------------------------------

    def test_a_filter_that_hides_nothing_is_not_described_as_filtering(self):
        """`applied` is not "is a filter configured". A 0-100 range with no ignored genes is
        configured and hides nothing, and calling that "filtered" teaches people to ignore the
        word."""
        self._filter(min_cutoff=0, max_cutoff=100)

        summary = self.describe()

        self.assertFalse(summary["applied"])
        self.assertEqual([], summary["cutoffs"])
        self.assertEqual([], summary["genes"])

    def test_a_frequency_floor_is_reported(self):
        self._filter(min_cutoff=20, max_cutoff=100)

        summary = self.describe()

        self.assertTrue(summary["applied"])
        self.assertEqual([(20, 100)], summary["cutoffs"])

    def test_a_ceiling_is_reported_too(self):
        self._filter(min_cutoff=0, max_cutoff=90)

        self.assertEqual([(0, 90)], self.describe()["cutoffs"])

    def test_ignored_genes_are_reported_by_name(self):
        self._filter(ignored_genes="thrA, ilvG")

        summary = self.describe()

        self.assertTrue(summary["applied"])
        self.assertEqual(["ilvG", "thrA"], summary["genes"])

    def test_no_filter_row_at_all_is_not_filtering(self):
        """An experiment gets its row from a rebuild. Before that runs there is nothing to
        describe, and the honest answer is that nothing is hidden."""
        summary = self.describe()

        self.assertFalse(summary["applied"])
        self.assertEqual(0, summary["experiments"])

    def test_showing_filtered_rows_is_reported_as_skipped(self):
        """A page viewed with Show Experiment Filtered ticked must not claim to be hiding
        what it is in fact showing."""
        self._filter(min_cutoff=20)

        summary = self.describe(skip_experiment_filter=True)

        self.assertTrue(summary["skipped"])
        self.assertFalse(summary["applied"])

    # --- the one that earns the shared resolution -----------------------------------------

    def test_the_summary_and_the_queryset_agree(self):
        """Same fixture, same filters, both derived from `filters_in_play`.

        If a future change resolves the description separately, this is what catches it: the
        page would go on describing a filter while the rows behind it stopped matching, and no
        other test compares the two.
        """
        low = self._observe("0.0100")
        kept = self._observe("0.9000", position=200)

        # Nothing configured: the summary says so and the rows agree.
        self._filter(min_cutoff=0, max_cutoff=100)
        self.assertFalse(self.describe()["applied"])
        surviving = filter_observed_mutations(
            ObservedMutation.objects.all(), self.experiment.ale_id)
        self.assertEqual(2, len(surviving))

        # A floor: the summary says something is hidden, and something is.
        self._filter(min_cutoff=20, max_cutoff=100)
        self.assertTrue(self.describe()["applied"])
        surviving = filter_observed_mutations(
            ObservedMutation.objects.all(), self.experiment.ale_id)
        self.assertEqual([kept.id], [o.id for o in surviving])
        self.assertNotIn(low.id, [o.id for o in surviving])

    def test_a_gene_filter_agrees_too(self):
        """The gene half is applied in Python rather than SQL, so it is worth its own case."""
        thra = self._observe("0.9000", gene="thrA")
        ilvg = self._observe("0.9000", gene="ilvG", position=200)
        self._filter(ignored_genes="thrA")

        summary = self.describe()
        surviving = filter_observed_mutations(
            ObservedMutation.objects.all(), self.experiment.ale_id)

        self.assertEqual(["thrA"], summary["genes"])
        self.assertEqual([ilvg.id], [o.id for o in surviving])
        self.assertNotIn(thra.id, [o.id for o in surviving])


class FilterSummaryTagTestCase(TestCase):
    """`{% filter_summary %}` -- what makes this generic.

    It reads the context every table page already sets, so including it once in
    `base_table_template.html` reaches aledb-compare, aledb-fixation and aledb-converge with no
    change to any of their repositories.
    """

    def render(self, context):
        from django.template import Context, Template

        return Template(
            "{% load filter_summary %}{% filter_summary %}").render(Context(context))

    def test_it_renders_with_no_experiment_selected(self):
        """Every table page can be reached before an experiment is chosen, and a template tag
        that raised there would take the page down with it."""
        self.assertIn("No filtering", self.render({}))

    def test_it_reads_the_experiment_from_the_context(self):
        experiment = AleExperiment.objects.create(instrument=Instrument.objects.create())
        AleExperimentFilter.objects.create(ale_experiment=experiment, min_cutoff=25,
                                           max_cutoff=100)

        markup = self.render({"ale_experiment_id": experiment.ale_id})

        self.assertIn("25", markup)
        self.assertIn("Change", markup, "it links to the filter page")

    def test_it_reads_show_experiment_filtered_too(self):
        experiment = AleExperiment.objects.create(instrument=Instrument.objects.create())
        AleExperimentFilter.objects.create(ale_experiment=experiment, min_cutoff=25,
                                           max_cutoff=100)

        markup = self.render({"ale_experiment_id": experiment.ale_id,
                              "show_exp_filtered": True})

        self.assertIn("Showing everything", markup)

    def test_own_rules_replaces_the_description(self):
        """For a page that does not use the experiment filter at all. Rendering an empty
        summary there would read as "no filtering here" when the truth is "different filtering
        here"."""
        from django.template import Context, Template

        markup = Template(
            '{% load filter_summary %}{% filter_summary own_rules="Its own rules." %}'
        ).render(Context({}))

        self.assertIn("Its own rules.", markup)
        self.assertNotIn("No filtering", markup)


class SharedTableTestCase(TestCase):
    """The include is in the shared template, which is the whole leverage."""

    def test_the_shared_table_template_includes_the_summary(self):
        import io
        import os

        from aledb_common import __file__ as common_file

        path = os.path.join(os.path.dirname(common_file), "templates",
                            "base_table_template.html")
        markup = io.open(path, encoding="utf-8").read()

        self.assertIn("{% load filter_summary %}", markup)
        self.assertIn("{% filter_summary %}", markup)


class SummaryWordingTestCase(TestCase):
    """The sentence itself, because it is what a reader acts on."""

    def render(self, context):
        from django.template import Context, Template

        markup = Template(
            "{% load filter_summary %}{% filter_summary %}").render(Context(context))
        import re
        return " ".join(re.sub(r"<[^>]+>", "", markup).split())

    def _experiment(self, **fields):
        experiment = AleExperiment.objects.create(instrument=Instrument.objects.create())
        fields.setdefault("min_cutoff", 0)
        fields.setdefault("max_cutoff", 100)
        AleExperimentFilter.objects.create(ale_experiment=experiment, **fields)
        return experiment.ale_id

    def test_it_does_not_mention_a_checkbox_the_page_does_not_have(self):
        """Only Compare renders Show Experiment Filtered. Fixation, Converge and the
        per-sample table read the same filter and have no such control, and telling somebody
        to tick one that is not there is worse than staying quiet."""
        experiment = self._experiment(min_cutoff=20)

        without = self.render({"ale_experiment_id": experiment})
        with_toggle = self.render({"ale_experiment_id": experiment,
                                   "show_filter_toggles": True})

        self.assertNotIn("Show Experiment Filtered", without)
        self.assertIn("Show Experiment Filtered", with_toggle)

    def test_genes_alone_read_as_a_sentence(self):
        experiment = self._experiment(ignored_genes="thrA")

        self.assertIn("Filtered, excluding 1 gene (thrA).",
                      self.render({"ale_experiment_id": experiment}))

    def test_cutoffs_and_genes_together_read_as_a_sentence(self):
        experiment = self._experiment(min_cutoff=20, ignored_genes="thrA, ilvG")

        text = self.render({"ale_experiment_id": experiment})

        self.assertIn("Filtered to frequencies 20&ndash;100%, and excluding 2 genes", text)

    def test_a_cutoff_alone_reads_as_a_sentence(self):
        experiment = self._experiment(min_cutoff=20)

        self.assertIn("Filtered to frequencies 20&ndash;100%.",
                      self.render({"ale_experiment_id": experiment}))

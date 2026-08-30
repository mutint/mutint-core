"""The needle plot's data, computed rather than stored.

`/stats` draws one needle per observed mutation at `{coord, category, value}`. That list was
held in `StaticData` as a JSON blob, precomputed at import since long before there was a
rebuild registry, and it is now built by the request that renders it -- from three columns as
tuples rather than from every observation as a model instance.

Two things are worth pinning and neither was tested before:

  * **the filter reaches it**, both halves. The frequency cutoff is SQL and the ignored-gene
    list is a set-subset test walked per row, and the plot has to agree with the count tables
    beside it on the same page or the page contradicts itself;
  * **the order is the sample order**, which is what makes the computed list comparable to
    the stored one it replaced rather than merely equivalent as a set.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_seq.models import (ExperimentReference, Mutation, ObservedMutation,
                              ResequencingExperiment)
from aledb_stats.util import get_needle_plot_data, needle_plot_axis


class NeedlePlotTestCase(TestCase):
    """Its own fixture, deliberately.

    Subclassing `SummaryTestCase` would inherit its tests and run every one of them a second
    time under this name -- which has happened twice in this repo and is silent, because the
    duplicates pass.
    """

    def setUp(self):
        self.user = User.objects.create(username="needle", email="n@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        from aledb_import.gd_import import prepare_experiment_by_id
        self.context = prepare_experiment_by_id(self.experiment.ale_id)

        self.first = self._sample(ale=1, flask=100)
        self.second = self._sample(ale=2, flask=100)

    def _sample(self, ale, flask):
        ale_row, _ = AleId.objects.get_or_create(
            ale_experiment=self.experiment, ale_id=ale)
        flask_row, _ = Flask.objects.get_or_create(
            ale_id=ale_row, flask_number=flask,
            defaults={"media": self.context["media"]})
        isolate = Isolate.objects.create(
            flask=flask_row, isolate_number=1, is_population=False,
            freezer_box=self.context["freezer_box"])
        tech_rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        return ResequencingExperiment.objects.create(
            tech_rep=tech_rep, sample_name="%d-%d-1-1" % (ale, flask))

    def _mutation(self, mutation_type, position, gene):
        return Mutation.objects.create(
            ale_experiment=self.experiment, mutation_type=mutation_type, position=position,
            sequence_change="A>T", protein_change="", gene=gene)

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

    def _needles(self):
        return get_needle_plot_data(self.experiment.ale_id)

    # ---- the shape -------------------------------------------------------------------
    def test_one_needle_per_observation(self):
        """Per observation, not per mutation: a mutation seen in two samples is two
        needles, which is what makes the plot's height mean anything."""
        shared = self._mutation("SNP", 150, gene="thrA")
        self._observe(self.first, shared)
        self._observe(self.second, shared)

        self.assertEqual([{'coord': '150', 'category': 'SNP', 'value': 1},
                          {'coord': '150', 'category': 'SNP', 'value': 1}],
                         self._needles())

    def test_the_coordinate_is_a_string(self):
        """`coord` is rendered into JavaScript by the template. It has always been a string
        and the plot's axis handling depends on it."""
        self._observe(self.first, self._mutation("DEL", 4321, gene="araB"))

        self.assertEqual([{'coord': '4321', 'category': 'DEL', 'value': 1}], self._needles())

    def test_the_order_is_the_sample_order(self):
        """ALE 1's needle before ALE 2's, whatever order the rows were created in."""
        self._observe(self.second, self._mutation("INS", 900, gene="lacZ"))
        self._observe(self.first, self._mutation("MOB", 100, gene="thrA"))

        self.assertEqual(['100', '900'], [n['coord'] for n in self._needles()])

    def test_an_experiment_with_no_mutations_draws_nothing(self):
        self.assertEqual([], self._needles())

    # ---- the filter deliberately does not reach it -----------------------------------
    def test_it_is_not_filtered(self):
        """**`/stats` shows what the experiment holds, not what you are reading through.**

        It applied the shared experiment filter until that filter became a per-reader one, and
        the Overview and the needle plot are summaries of a dataset in the way the dashboard
        is -- so they were left out. Three tests stood here pinning that a frequency cutoff and
        an ignored-gene list reached this plot; they described a filter that no longer has a
        stored value to come from, and this is the property that replaced them.
        """
        self._observe(self.first, self._mutation("SNP", 150, gene="rrlA"), frequency="0.0100")
        self._observe(self.first, self._mutation("SNP", 250, gene="thrA"))

        self.assertEqual(['150', '250'], sorted(n['coord'] for n in self._needles()),
                         "the needle plot has started filtering")

    def test_it_takes_no_filter_argument(self):
        """Structural rather than incidental: there is no way to hand this one."""
        import inspect

        self.assertNotIn("view_filter",
                         inspect.signature(get_needle_plot_data).parameters)

    # ---- nothing is stored -----------------------------------------------------------
    def test_it_reflects_a_new_observation_immediately(self):
        """`StaticData` was rebuilt through the 'static_data' rebuilder, so a new observation
        appeared only once something marked it stale. There is no such window now, and that
        is the behaviour the rebuilder was traded for."""
        self._observe(self.first, self._mutation("SNP", 150, gene="thrA"))
        self.assertEqual(1, len(self._needles()))

        self._observe(self.second, self._mutation("SNP", 250, gene="thrA"))
        self.assertEqual(2, len(self._needles()))


class NeedlePlotAxisTestCase(TestCase):
    """Which sequence the plot is about, and how long it is.

    The axis was a hardcoded `maxCoord: 5000000` -- roughly E. coli, wrong for anything else --
    while the points carried no sequence name, so a multi-contig reference drew every contig on
    top of itself. Both rendered a perfectly ordinary-looking plot of the wrong genome.
    """

    def setUp(self):
        self.user = User.objects.create(username="axis", email="a@e.com", is_active=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="axis")
        self.experiment = ResequencingExperiment.objects.get().ale_experiment

    def test_the_length_comes_from_the_stored_reference(self):
        axis = needle_plot_axis(self.experiment.ale_id)
        reference = ExperimentReference.objects.get(ale_experiment=self.experiment)
        entry = next(e for e in reference.seq_ids if e["id"] == axis["contig"])
        self.assertEqual(entry["length"], axis["length"])
        self.assertNotEqual(5000000, axis["length"])

    def test_it_names_the_contig_the_mutations_are_on(self):
        axis = needle_plot_axis(self.experiment.ale_id)
        self.assertEqual(
            set(Mutation.objects.values_list("reseq_reference", flat=True)),
            {axis["contig"]})
        self.assertEqual(1, axis["contig_count"])

    def test_the_data_is_scoped_to_that_contig(self):
        """Two contigs' positions on one unlabelled axis is a plot of nothing."""
        self.assertEqual([], get_needle_plot_data(self.experiment.ale_id, contig="other"))
        self.assertTrue(get_needle_plot_data(
            self.experiment.ale_id, contig=needle_plot_axis(self.experiment.ale_id)["contig"]))

    def test_no_reference_leaves_the_length_unknown_rather_than_guessed(self):
        """The page then falls back to the data's own extent, which is still truer than a
        constant -- an experiment imported from bare .gd files has no reference at all."""
        ExperimentReference.objects.filter(ale_experiment=self.experiment).delete()
        axis = needle_plot_axis(self.experiment.ale_id)
        self.assertIsNone(axis["length"])
        self.assertIsNotNone(axis["contig"])

    def test_an_experiment_with_no_mutations_names_no_contig(self):
        ObservedMutation.objects.all().delete()
        axis = needle_plot_axis(self.experiment.ale_id)
        self.assertIsNone(axis["contig"])
        self.assertEqual(0, axis["contig_count"])

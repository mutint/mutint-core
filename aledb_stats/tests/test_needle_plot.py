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
        self.assertEqual([axis["contig"]], [e["id"] for e in axis["contigs"]])

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

    def test_an_experiment_with_no_mutations_still_names_its_reference_sequence(self):
        """The sequences come from the reference, so they are known whether or not anything
        was found on them -- and an empty axis over the right genome is an answer."""
        ObservedMutation.objects.all().delete()
        axis = needle_plot_axis(self.experiment.ale_id)
        self.assertIsNotNone(axis["contig"])
        self.assertEqual([0], [e["count"] for e in axis["contigs"]])

    def test_with_neither_a_reference_nor_mutations_there_is_nothing_to_name(self):
        ObservedMutation.objects.all().delete()
        ExperimentReference.objects.filter(ale_experiment=self.experiment).delete()
        axis = needle_plot_axis(self.experiment.ale_id)
        self.assertIsNone(axis["contig"])
        self.assertEqual([], axis["contigs"])


class ContigPickerTestCase(TestCase):
    """A multi-contig reference offers every one of its sequences, longest first.

    The plot draws one sequence, and that sequence used to be decided for the reader: the
    contig with the most observations was hardcoded as the answer rather than as the default,
    so a plasmid's mutations were on no page in the product at all. The page said which contig
    it was drawing, which made the omission visible without making it fixable.

    Two rules meet here and are tested apart, because a fixture where they agree would pass
    under either: the *list* is every sequence the reference has, mutations or none, and the
    *default* is the longest of them rather than the busiest.
    """

    #: Deliberately more mutations on the short sequence than on the long one: the default is
    #: the longest, and a fixture where the two rules agree would pass under either.
    GD_TEXT = ("#=GENOME_DIFF\t1.0\n"
               "#=REFSEQ\ttest_ref\n"
               "SNP\t1\t.\tchrom\t100\tA\tgene_name=thrA\tfrequency=1\n"
               "SNP\t2\t.\tplasmid\t40\tT\tgene_name=bla\tfrequency=1\n"
               "DEL\t3\t.\tplasmid\t60\t5\tgene_name=tet\tfrequency=0.5\n")

    def setUp(self):
        self.user = User.objects.create(username="picker", email="p@e.com", is_active=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(
            self.drop, "s1",
            sequences=[("chrom", breseq_fixture.SEQUENCE_A),
                       ("plasmid", breseq_fixture.SEQUENCE_B)],
            gd_text=self.GD_TEXT)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="picker")
        self.experiment = ResequencingExperiment.objects.get().ale_experiment

    def test_every_sequence_is_offered_longest_first(self):
        axis = needle_plot_axis(self.experiment.ale_id)

        self.assertEqual([("chrom", 1), ("plasmid", 2)],
                         [(e["id"], e["count"]) for e in axis["contigs"]])

    def test_the_default_is_the_longest_sequence_not_the_busiest(self):
        """The chromosome is what somebody opening an experiment means by "the genome". A
        small plasmid under strong selection can carry more mutations than it, and the page
        opening on the plasmid would be a surprise about the reference dressed up as a fact
        about the data. Length is a property of the reference; a count moves with the data."""
        axis = needle_plot_axis(self.experiment.ale_id)

        self.assertEqual("chrom", axis["contig"])
        self.assertEqual(2, max(e["count"] for e in axis["contigs"]),
                         "the busiest sequence is the one not chosen")
        self.assertEqual(1, dict(
            (e["id"], e["count"]) for e in axis["contigs"])[axis["contig"]])

    def test_each_offered_contig_carries_its_own_length(self):
        """The axis has to change with the contig, or a 4.6 Mb chromosome's scale is used to
        draw a plasmid and every mutation lands in the leftmost pixel -- which is the fault
        the hardcoded 5 Mb axis had, one level down."""
        axis = needle_plot_axis(self.experiment.ale_id)

        lengths = {e["id"]: e["length"] for e in axis["contigs"]}
        self.assertEqual(len(breseq_fixture.SEQUENCE_A), lengths["chrom"])
        self.assertEqual(len(breseq_fixture.SEQUENCE_B), lengths["plasmid"])

    def test_choosing_a_contig_moves_the_axis_and_the_data(self):
        axis = needle_plot_axis(self.experiment.ale_id, "plasmid")

        self.assertEqual("plasmid", axis["contig"])
        self.assertEqual(len(breseq_fixture.SEQUENCE_B), axis["length"])
        self.assertEqual(
            ["40", "60"],
            sorted(point["coord"] for point in
                   get_needle_plot_data(self.experiment.ale_id, axis["contig"])))

    def test_an_unknown_contig_falls_back_to_the_default(self):
        """A hand-typed or stale name draws the default rather than an empty plot: the
        picker is a view control, not an identity, and an empty plot of a contig that does
        not exist is indistinguishable from one that has no mutations."""
        axis = needle_plot_axis(self.experiment.ale_id, "no-such-contig")

        self.assertEqual("chrom", axis["contig"])

    def test_a_sequence_with_no_mutations_is_still_offered(self):
        """Its plot is an empty axis, which is an answer -- the reader asked what is on the
        plasmid and the page says nothing is. Left out of the menu it would be
        indistinguishable from a sequence this reference does not have, and the count beside
        the name is what tells those apart."""
        ObservedMutation.objects.filter(mutation__reseq_reference="plasmid").delete()

        axis = needle_plot_axis(self.experiment.ale_id)

        self.assertEqual([("chrom", 1), ("plasmid", 0)],
                         [(e["id"], e["count"]) for e in axis["contigs"]])
        self.assertEqual(
            [], get_needle_plot_data(self.experiment.ale_id, "plasmid"))

    def test_a_contig_the_reference_does_not_have_is_offered_last(self):
        """A mutation can name a contig the stored reference does not list. It has no length,
        so it sorts below everything that does -- but dropping it would leave mutations the
        experiment holds on no axis at all."""
        Mutation.objects.filter(reseq_reference="plasmid").update(reseq_reference="contig9")

        axis = needle_plot_axis(self.experiment.ale_id)

        self.assertEqual(["chrom", "plasmid", "contig9"],
                         [e["id"] for e in axis["contigs"]])
        self.assertIsNone(axis["contigs"][-1]["length"])

    def test_the_page_renders_the_chosen_contig(self):
        self.client.force_login(self.user)
        self.experiment.project.user = self.user
        self.experiment.project.save()

        # follow=True: `/stats` is an APPEND_SLASH redirect, as every other test of this
        # page has to do too.
        response = self.client.get(
            "/stats?ale_experiment_id=%s&contig=plasmid" % self.experiment.ale_id,
            follow=True)

        self.assertEqual(200, response.status_code)
        body = response.content.decode()
        self.assertIn("contig_picker", body)
        self.assertIn("contig=plasmid", body)

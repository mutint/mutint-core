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

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_filter.models import AleExperimentFilter
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment
from aledb_stats.util import get_needle_plot_data


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
        from aledb_filter.util import ensure_default_experiment_filter

        ensure_default_experiment_filter(self.experiment.ale_id)
        AleExperimentFilter.objects.filter(ale_experiment=self.experiment).update(**fields)

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

    # ---- the filter reaches it -------------------------------------------------------
    def test_a_frequency_cutoff_excludes(self):
        """The SQL half. The plot and the Overview's counts sit in one viewport and would
        contradict each other if only one of them applied this."""
        self._observe(self.first, self._mutation("SNP", 150, gene="thrA"), frequency="0.0100")
        self._observe(self.first, self._mutation("SNP", 250, gene="thrA"))
        self._filter(min_cutoff=50)

        self.assertEqual(['250'], [n['coord'] for n in self._needles()])

    def test_an_ignored_gene_excludes(self):
        """The half with no SQL: this is the branch walked per row."""
        self._observe(self.first, self._mutation("SNP", 150, gene="rrlA"))
        self._observe(self.first, self._mutation("SNP", 250, gene="thrA"))
        self._filter(ignored_genes="rrlA")

        self.assertEqual(['250'], [n['coord'] for n in self._needles()])

    def test_a_partly_ignored_intergenic_mutation_survives(self):
        """Subset, not intersection -- the same rule `gene_is_filtered` applies everywhere,
        asserted here because this caller reaches it with a tuple rather than a model."""
        self._observe(self.first, self._mutation("DEL", 150, gene="thrB/thrC"))
        self._filter(ignored_genes="thrB")

        self.assertEqual(['150'], [n['coord'] for n in self._needles()])

    # ---- nothing is stored -----------------------------------------------------------
    def test_it_reflects_a_new_observation_immediately(self):
        """`StaticData` was rebuilt through the 'static_data' rebuilder, so a new observation
        appeared only once something marked it stale. There is no such window now, and that
        is the behaviour the rebuilder was traded for."""
        self._observe(self.first, self._mutation("SNP", 150, gene="thrA"))
        self.assertEqual(1, len(self._needles()))

        self._observe(self.second, self._mutation("SNP", 250, gene="thrA"))
        self.assertEqual(2, len(self._needles()))

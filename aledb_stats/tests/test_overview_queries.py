"""The Overview must not issue more queries because an experiment has more samples.

Nothing in the suite could see this before: every existing test of `/stats` uses one sample
and no mutations, so the two per-sample queries the page fired -- one for each sample's
missing-coverage evidence, one for its mutation count -- were invisible, and both fired
during *template rendering*, after the view's own "stats performance" log had been written.

These tests are about the shape of the cost rather than its size, so they compare two
experiments rather than assert a number. A literal query count would be a tripwire for every
unrelated change to a context processor; the invariant that matters is that the count does
not grow with the data.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, TechnicalReplicate,
)
from aledb_seq.models import Mutation, ObservedMutation, ResequencingExperiment


class OverviewQueryCountTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        # `aledb_filter.util.get_global_filter` is a `get_or_create(id=1)`, so the first page
        # view in the process inserts the row -- a write on a GET, and four extra queries
        # charged to whichever experiment happened to be measured first. Settle it here so
        # these comparisons measure the page rather than the order the tests run in.
        from aledb_filter.models import GlobalFilter
        GlobalFilter.objects.get_or_create(id=1)

    def _experiment(self, name, samples):
        created = self.client.post(
            "/ale/projects/create/", {"name": name, "experiment": name}).json()
        experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        from aledb_import.gd_import import prepare_experiment_by_id
        context = prepare_experiment_by_id(experiment.ale_id)

        ale = AleId.objects.create(ale_experiment=experiment, ale_id=1)
        for number in range(samples):
            flask = Flask.objects.create(ale_id=ale, flask_number=100 + number,
                                         media=context["media"])
            isolate = Isolate.objects.create(flask=flask, isolate_number=1,
                                             is_population=False,
                                             freezer_box=context["freezer_box"])
            tech_rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
            sample = ResequencingExperiment.objects.create(
                tech_rep=tech_rep, sample_name="1-%d-1-1" % (100 + number))
            mutation = Mutation.objects.create(
                ale_experiment=experiment, mutation_type="SNP", position=number,
                sequence_change="A>T", protein_change="nonsynonymous (A1T)", gene="thrA")
            ObservedMutation.objects.create(
                sequencing_experiment=sample, mutation=mutation,
                present=True, frequency="1.0000")
        return experiment

    def _queries(self, experiment):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                "/stats?ale_experiment_id=%d" % experiment.ale_id, follow=True)
        self.assertEqual(200, response.status_code)
        return len(captured)

    def test_a_warm_page_costs_the_same_however_many_samples(self):
        """The 2N fix. Both pages are warmed first so neither pays for a rebuild."""
        small = self._experiment("small", samples=1)
        large = self._experiment("large", samples=12)
        self._queries(small)
        self._queries(large)

        self.assertEqual(self._queries(small), self._queries(large),
                         "the Overview is issuing queries per sample")

    def test_a_cold_page_costs_the_same_however_many_samples(self):
        """Cold too: the rebuild aggregates, so it does not walk samples either."""
        small = self._experiment("small", samples=1)
        large = self._experiment("large", samples=12)

        self.assertEqual(self._queries(small), self._queries(large),
                         "the cold rebuild is issuing queries per sample")

    def test_the_second_view_is_cheaper_than_the_first(self):
        """The summary is what makes it cheaper -- the first view builds it, the rest read it."""
        experiment = self._experiment("counted", samples=6)
        cold = self._queries(experiment)
        warm = self._queries(experiment)

        self.assertLess(warm, cold, "the summary is being rebuilt on every view")

    def test_the_page_still_shows_the_right_counts(self):
        """A query-count test that stopped rendering the numbers would still pass."""
        experiment = self._experiment("shown", samples=3)
        html = self.client.get("/stats?ale_experiment_id=%d" % experiment.ale_id,
                               follow=True).content.decode()
        self.assertIn('<td class="mutation_count">1</td>', html)
        self.assertIn('<td class="missing_coverage_count">0</td>', html)

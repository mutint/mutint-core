"""The Overview must not issue more queries because an experiment has more samples.

Nothing in the suite could see this before: every existing test of `/stats` uses one sample
and no mutations, so the two per-sample queries the page fired -- one for each sample's
missing-coverage evidence, one for its mutation count -- were invisible, and both fired
during *template rendering*, after the view's own "stats performance" log had been written.

These tests are about the shape of the cost rather than its size, so they compare two
experiments rather than assert a number. A literal query count would be a tripwire for every
unrelated change to a context processor; the invariant that matters is that the count does
not grow with the data.

There used to be a cold page and a warm one, and a test that the second view was cheaper --
`ExperimentSummary` and `StaticData` were built by the first view and read by the rest.
Neither is stored now, so every view is a cold one, and the assertion that replaced it is the
stronger one: the count does not depend on how many times the page has been looked at either.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from mutint_experiment.models import (
    Experiment, Population,
)
from mutint_sample.models import Mutation, MutationCall, Sample


class OverviewQueryCountTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        # A warm-up used to be needed here: `get_global_filter` was a `get_or_create(id=1)`,
        # so the first page view in the process inserted a row -- a write on a GET, charged to
        # whichever experiment happened to be measured first. The global filter is gone and
        # with it that asymmetry, so these comparisons no longer depend on test order.

    def _experiment(self, name, samples):
        created = self.client.post(
            "/project/create/", {"name": name, "experiment": name}).json()
        experiment = Experiment.objects.get(pk=created["experiment_id"])

        from mutint_import.gd_import import prepare_experiment_by_id
        context = prepare_experiment_by_id(experiment.id)

        ale = Population.objects.create(experiment=experiment, name=1)
        for number in range(samples):
            sample = Sample.objects.create(
                population=ale, time_point=100 + number, name="1-1", is_clonal=True,
                source_name="1-%d-1-1" % (100 + number))
            mutation = Mutation.objects.create(
                experiment=experiment, mutation_type="SNP", start_position=number,
                sequence_change="A>T", protein_change="nonsynonymous (A1T)", gene="thrA")
            MutationCall.objects.create(
                sample=sample, mutation=mutation,
                present=True, frequency="1.0000")
        # The one thing on this page that *is* stored and rebuilt: the Storage panel's sizes
        # (`mutint_common.storage_registry`), measured by the first reader after a change,
        # deliberately -- walking a report tree per view is the cost storing exists to
        # avoid. Measured here so the counts below are about the Overview's own numbers,
        # which store nothing, and not about that panel paying once.
        from mutint_common.storage_registry import ensure_measured
        ensure_measured(experiment.id)
        return experiment

    def _queries(self, experiment):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                "/stats?experiment_id=%d" % experiment.id, follow=True)
        self.assertEqual(200, response.status_code)
        return len(captured)

    def test_the_cost_does_not_grow_with_the_sample_count(self):
        """The 2N fix. Twelve samples must cost what one does.

        This was two tests, one warming the pages first and one not, back when the first view
        of a page built two tables and later views read them. With nothing stored there is
        only one measurement to make.
        """
        small = self._experiment("small", samples=1)
        large = self._experiment("large", samples=12)

        self.assertEqual(self._queries(small), self._queries(large),
                         "the Overview is issuing queries per sample")

    def test_every_view_costs_the_same(self):
        """No view is a rebuild, so no view is dearer than the next.

        The replacement for a test that the *second* view was cheaper. That one passed on a
        cache being filled; this one fails if a cache comes back -- either as a first view
        that pays extra, or as a later view that pays less because something was stored.
        """
        experiment = self._experiment("counted", samples=6)

        counts = [self._queries(experiment) for _ in range(4)]

        self.assertEqual([counts[0]] * 4, counts,
                         "a view is doing work the others are not")

    def test_the_page_still_shows_the_right_counts(self):
        """A query-count test that stopped rendering the numbers would still pass."""
        experiment = self._experiment("shown", samples=3)
        html = self.client.get("/stats?experiment_id=%d" % experiment.id,
                               follow=True).content.decode()
        self.assertIn('<td class="mutation_count">1</td>', html)
        # Bases, not a count of regions. These samples have neither, so it is 0 -- and no
        # percentage beside it, because the fixture establishes no reference and a share of
        # an unknown genome is not 0%.
        self.assertIn('<td class="uncalled_bases">0', html)
        self.assertNotIn("%)", html.split('class="uncalled_bases"')[1][:120])

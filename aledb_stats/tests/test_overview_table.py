"""The Sample Resequencing Stats table on the experiment overview page.

These three values come off breseq's summary.json as raw floats and reach the template
untouched -- real data holds `68.0388108058057` and `139.540777568556`. Rendered as-is that
is a dozen digits of false precision in a column nobody can scan.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (
    Experiment, Population, TimePoint, Project,
)
from aledb_seq.models import Sample


class OverviewTableTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        # Through the view: Project.objects.create leaves no guardian grant and the
        # experiment's pages then 403.
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

        from aledb_import.gd_import import prepare_experiment_by_id
        context = prepare_experiment_by_id(self.experiment.id)

        ale = Population.objects.create(experiment=self.experiment, name=1)
        flask = TimePoint.objects.create(population=ale, value=30000,
                                     media=context["media"])
        self.sample = Sample.objects.create(
            time_point=flask, name="1-1", is_population=False,
            source_name="1-30000-1-1",
            mean_coverage=68.0388108058057,
            percentage_mapped=95.5735575027531,
            average_read_length=139.540777568556,
            reads=1234567)

    def _html(self):
        return self.client.get(
            "/stats?ale_experiment_id=%d" % self.experiment.id,
            follow=True).content.decode()

    def test_the_headings_are_the_short_ones(self):
        html = self._html()
        for heading in ("<th>Coverage</th>", "<th>Mapped</th>", "<th>Read Length</th>"):
            self.assertIn(heading, html)

    def test_the_long_headings_are_gone(self):
        html = self._html()
        for heading in ("Mean Coverage", "Percent Mapped", "Average Read Length"):
            self.assertNotIn(heading, html)

    def test_each_value_is_rounded_to_one_decimal(self):
        html = self._html()
        self.assertIn(">68.0<", html)
        self.assertIn(">95.6%<", html)     # 95.5735... rounds up; the cell keeps its %
        self.assertIn(">139.5<", html)

    def test_the_raw_floats_do_not_reach_the_page(self):
        """The point of the change: without floatformat these render in full."""
        html = self._html()
        for raw in ("68.0388108058057", "95.5735575027531", "139.540777568556"):
            self.assertNotIn(raw, html)

    def test_total_reads_is_left_alone(self):
        """It is an IntegerField; a count has no decimal place to round to."""
        self.assertIn(">1234567<", self._html())

    def test_a_whole_number_still_shows_its_decimal(self):
        """floatformat:1 rather than -1, so the column stays aligned when a value
        happens to land on a whole number -- which every un-imported sample does."""
        self.sample.mean_coverage = 42
        self.sample.save()
        self.assertIn(">42.0<", self._html())

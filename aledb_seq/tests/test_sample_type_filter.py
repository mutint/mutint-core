"""`?sample_type=` selects clonal or mixed samples, or -- for anything else -- neither.

The bug this pins down: `get_sample_type` returned whatever the query string said, and
`get_ordered_reseq_queryset` read anything that was not the mixed token as *clonal*. So
`?sample_type=anything` returned half the samples while the picker still read "All sample
types" -- a page subset without saying so, and the failure mode of a stale bookmark once the
accepted values change.

Both polarities are asserted at every read site on purpose. A test that checks only the
clonal case passes unchanged under a global inversion of the flag, which is exactly the
mistake the upcoming `is_clonal` rename can make.
"""

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from aledb_common.constants import (
    REQUEST_ALL, SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED,
)
from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Media, Project,
)
from aledb_seq.models import ResequencingExperiment
from aledb_seq.util import get_ordered_reseq_queryset
from aledb_seq.views.common import get_sample_type


class SampleTypeParsingTestCase(TestCase):

    def parse(self, query):
        return get_sample_type(RequestFactory().get("/x" + query))

    def test_recognised_values_pass_through(self):
        self.assertEqual(self.parse("?sample_type=clonal"), SAMPLE_TYPE_CLONAL)
        self.assertEqual(self.parse("?sample_type=population"), SAMPLE_TYPE_MIXED)

    def test_absent_empty_and_all_mean_no_filter(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("?sample_type="))
        self.assertIsNone(self.parse("?sample_type=" + REQUEST_ALL))

    def test_unrecognised_value_means_no_filter(self):
        """Not "clonal", which is what it used to mean."""
        with self.assertLogs("aledb_seq.views.common", level="WARNING"):
            self.assertIsNone(self.parse("?sample_type=mixed"))


class SampleTypeFilterTestCase(TestCase):

    def setUp(self):
        user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        project = Project.objects.create(name="P", user=user)
        self.experiment = AleExperiment.objects.create(name="E", project=project)
        media = Media.objects.create(description="M9")
        ale = AleId.objects.create(ale_experiment=self.experiment, ale_id="1")
        flask = Flask.objects.create(ale_id=ale, flask_number=1000, media=media)
        self.clonal = self.make_sample(flask, 1, is_population=False)
        self.mixed = self.make_sample(flask, 2, is_population=True)

    def make_sample(self, flask, number, *, is_population):
        return ResequencingExperiment.objects.create(
            flask=flask, isolate_number=number, is_population=is_population,
            sample_name="s%d" % number)

    def selected(self, sample_type):
        return set(get_ordered_reseq_queryset(
            self.experiment.id, sample_type=sample_type).values_list("pk", flat=True))

    def test_clonal_selects_only_the_clonal_sample(self):
        self.assertEqual(self.selected(SAMPLE_TYPE_CLONAL), {self.clonal.pk})

    def test_mixed_selects_only_the_mixed_sample(self):
        self.assertEqual(self.selected(SAMPLE_TYPE_MIXED), {self.mixed.pk})

    def test_no_filter_selects_both(self):
        self.assertEqual(self.selected(None), {self.clonal.pk, self.mixed.pk})

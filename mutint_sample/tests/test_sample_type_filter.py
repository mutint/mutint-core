"""`?sample_type=` selects clonal or mixed samples, or -- for anything else -- neither.

The bug this pins down: `get_sample_type` returned whatever the query string said, and
`get_ordered_sample_queryset` read anything that was not the mixed token as *clonal*. So
`?sample_type=anything` returned half the samples while the picker still read "All sample
types" -- a page subset without saying so, and the failure mode of a stale bookmark once the
accepted values change.

Both polarities are asserted at every read site on purpose. A test that checks only the
clonal case passes unchanged under a global inversion of the flag, which is exactly the
mistake the upcoming `is_clonal` rename can make.
"""

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from mutint_common.constants import (
    REQUEST_ALL, SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED,
)
from mutint_experiment.models import (
    Experiment, Population, Project,
)
from mutint_sample.models import Sample
from mutint_sample.util import get_ordered_sample_queryset
from mutint_sample.views.common import get_sample_type


class SampleTypeParsingTestCase(TestCase):

    def parse(self, query):
        return get_sample_type(RequestFactory().get("/x" + query))

    def test_recognized_values_pass_through(self):
        self.assertEqual(self.parse("?sample_type=clonal"), SAMPLE_TYPE_CLONAL)
        self.assertEqual(self.parse("?sample_type=mixed"), SAMPLE_TYPE_MIXED)

    def test_absent_empty_and_all_mean_no_filter(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("?sample_type="))
        self.assertIsNone(self.parse("?sample_type=" + REQUEST_ALL))

    def test_the_retired_token_means_no_filter(self):
        """`population` was the mixed token until this rename, so this is the stale
        bookmark -- the reason `get_sample_type` refuses what it does not recognize instead
        of passing it through. Passing it through would have shown half the samples with
        the picker still reading "All sample types"."""
        with self.assertLogs("mutint_sample.views.common", level="WARNING"):
            self.assertIsNone(self.parse("?sample_type=population"))

    def test_any_other_unrecognized_value_means_no_filter_too(self):
        with self.assertLogs("mutint_sample.views.common", level="WARNING"):
            self.assertIsNone(self.parse("?sample_type=nonsense"))


class SampleTypeFilterTestCase(TestCase):

    def setUp(self):
        user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        project = Project.objects.create(name="P", user=user)
        self.experiment = Experiment.objects.create(name="E", project=project)
        self.ale = Population.objects.create(experiment=self.experiment, name="1")
        self.clonal = self.make_sample(1, is_mixed=False)
        self.mixed = self.make_sample(2, is_mixed=True)

    def make_sample(self, number, *, is_mixed):
        return Sample.objects.create(
            population=self.ale, time_point=1000, name=number, is_clonal=not is_mixed,
            source_name="s%d" % number)

    def selected(self, sample_type):
        return set(get_ordered_sample_queryset(
            self.experiment.id, sample_type=sample_type).values_list("pk", flat=True))

    def test_clonal_selects_only_the_clonal_sample(self):
        self.assertEqual(self.selected(SAMPLE_TYPE_CLONAL), {self.clonal.pk})

    def test_mixed_selects_only_the_mixed_sample(self):
        self.assertEqual(self.selected(SAMPLE_TYPE_MIXED), {self.mixed.pk})

    def test_no_filter_selects_both(self):
        self.assertEqual(self.selected(None), {self.clonal.pk, self.mixed.pk})

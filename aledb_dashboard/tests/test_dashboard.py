"""What the installation-wide sample counts do and do not exclude.

These three tests used to pin the `STARTING_STRAIN_ALE_ID` rule: an ALE labelled `"0"` was
dropped from the ALE, flask and isolate counts. That label no longer means anything -- it was
one of four half-built spellings of "the ancestor", none of which subtracted a single mutation
-- and `Experiment.ancestor` replaced all of them.

So they pin the replacement instead, and the two halves worth keeping apart:

- a row whose samples are **all** the designated ancestor is not counted, which is what the
  ALE-0 rule was reaching for;
- a row with **no samples at all** is still counted, exactly as it always was. That is not an
  oversight. Changing it would move the published totals for a reason that has nothing to do
  with ancestors, and `prune_orphans` is what removes empty rows.
"""

from django.test import TestCase

from aledb_dashboard.models import SampleCounts
from aledb_dashboard.util import rebuild_sample_counts
from aledb_experiment.models import (Experiment, Population, TimePoint,
                                     Media)
from aledb_seq.models import Sample


class DashboardCountTestCase(TestCase):

    def setUp(self):
        self.experiment = Experiment.objects.create(
)

    def make_sample(self, ale_label, flask_number=1, isolate_number=1):
        """The full A/F/I chain, built by hand -- `gd_import` needs a reference and a store."""
        ale, _ = Population.objects.get_or_create(experiment=self.experiment,
                                             name=str(ale_label))
        flask, _ = TimePoint.objects.get_or_create(population=ale, value=flask_number,
                                               defaults={"media": Media.objects.create()})
        return Sample.objects.create(
            time_point=flask, is_population=False, name=str(isolate_number))

    def counts(self):
        rebuild_sample_counts()
        row = SampleCounts.objects.all()[0]
        return row.ale_count, row.flask_count, row.isolate_count


class TestEmptyRowsStillCount(DashboardCountTestCase):
    """An ALE labelled "0" is now an ALE like any other."""

    def test_bare_ale_rows_are_all_counted(self):
        for label in ("0", "9", "24", "1"):
            Population.objects.create(experiment=self.experiment, name=label)
        self.assertEqual(self.counts()[0], 4)

    def test_a_flask_under_ale_zero_is_counted(self):
        for label in ("0", "9"):
            ale = Population.objects.create(experiment=self.experiment, name=label)
            TimePoint.objects.create(media=Media.objects.create(), population=ale)
        ale_count, flask_count, _ = self.counts()
        self.assertEqual((ale_count, flask_count), (2, 2))


class TestAncestorIsNotCounted(DashboardCountTestCase):

    def test_a_row_holding_only_the_ancestor_drops_out(self):
        ancestor = self.make_sample("0")
        self.make_sample("1")
        self.experiment.set_ancestor(ancestor)

        # Two of everything exist; the ancestor's ALE, flask and isolate hold nothing else.
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_a_flask_holding_the_ancestor_and_another_sample_still_counts(self):
        """The rule is *every* sample beneath it, not *any*."""
        ancestor = self.make_sample("0", flask_number=1, isolate_number=1)
        self.make_sample("0", flask_number=1, isolate_number=2)
        self.experiment.set_ancestor(ancestor)

        ale_count, flask_count, isolate_count = self.counts()
        self.assertEqual((ale_count, flask_count), (1, 1))
        # The ancestor's own isolate is purely ancestral and goes; its sibling stays.
        self.assertEqual(isolate_count, 1)

    def test_nothing_is_dropped_when_no_ancestor_is_designated(self):
        self.make_sample("0")
        self.make_sample("1")
        self.assertEqual(self.counts(), (2, 2, 2))

"""Samples are ordered by ALE, then flask, then isolate. Everywhere.

`sample_order()` is the rule and every listing already goes through it. These tests exist for
the cases where "already goes through it" was doing quiet work: two of the three text fields
in a sample's coordinate sort as *text*, so the ordering is only right because of the padding
in `natural()`, and the failure looks like nothing at all -- `A1 F10 I1` above `A1 F2 I1` reads
as a list in some order rather than a list in the wrong one.

`sample_sort_key` is the Python form, used where a list is already in memory. It is tested
against `sample_order` rather than on its own, because the whole point is that they agree.
"""

from django.test import TestCase

from aledb_experiment.models import AleExperiment, AleId, Flask, Media
from aledb_experiment.ordering import sample_sort_key
from aledb_seq.models import ResequencingExperiment
from aledb_seq.util import get_ordered_reseq_queryset
from aledb_experiment import paths


class OrderingTestCase(TestCase):

    def setUp(self):
        self.experiment = AleExperiment.objects.create(
            name="E")
        self.media = Media.objects.create()

    def make(self, ale, flask, isolate="1"):
        ale_row, _ = AleId.objects.get_or_create(ale_experiment=self.experiment,
                                                 ale_id=str(ale))
        flask_row, _ = Flask.objects.get_or_create(ale_id=ale_row, flask_number=flask,
                                                   defaults={"media": self.media})
        return ResequencingExperiment.objects.create(
            flask=flask_row, isolate_number=str(isolate), is_population=False,
            sample_name="A%s F%s I%s" % (ale, flask, isolate))

    def order(self):
        return [r.sample_name for r in
                get_ordered_reseq_queryset(self.experiment.id, include_ancestor=True)]


class TestTheRule(OrderingTestCase):

    def test_ale_then_flask_then_isolate(self):
        self.make(2, 2, 2)
        self.make(1, 2, 1)
        self.make(2, 1, 1)
        self.make(1, 1, 2)
        self.make(1, 1, 1)
        self.assertEqual(self.order(),
                         ["A1 F1 I1", "A1 F1 I2", "A1 F2 I1",
                          "A2 F1 I1", "A2 F2 I2"])

    def test_flask_ten_comes_after_flask_two(self):
        """An integer column, so this one was never in doubt -- it is here as the control for
        the two below, which are the same claim over text."""
        self.make(1, 2)
        self.make(1, 10)
        self.assertEqual(self.order(), ["A1 F2 I1", "A1 F10 I1"])

    def test_ale_ten_comes_after_ale_two(self):
        """`AleId.ale_id` is text since 0008. Without `natural()` this is A10 then A2."""
        self.make(2, 1)
        self.make(10, 1)
        self.assertEqual(self.order(), ["A2 F1 I1", "A10 F1 I1"])

    def test_isolate_ten_comes_after_isolate_two(self):
        """The sample's `isolate_number` is text too, and an auto-numbered import gives one flask
        an isolate per sample -- fifty-one of them in the dev database's largest experiment."""
        self.make(1, 1, 2)
        self.make(1, 1, 10)
        self.assertEqual(self.order(), ["A1 F1 I2", "A1 F1 I10"])

    def test_a_lineage_label_sorts_as_text(self):
        """Real ALE labels are names, not numbers: `Ara-1` and `Ara+1` are different LTEE
        populations that both end in 1."""
        self.make("Ara-1", 1)
        self.make("Ara+1", 1)
        self.make(1, 1)
        self.assertEqual(self.order()[0], "A1 F1 I1")

    def test_a_null_flask_sorts_first_on_every_backend(self):
        """`flask_number` is nullable and `sample_order` says `nulls_first` rather than
        letting the backend decide -- PostgreSQL puts NULLs last ascending, SQLite first, so
        production and the test suite would otherwise disagree."""
        self.make(1, 1)
        self.make(1, None)
        self.assertEqual(self.order(), ["A1 FNone I1", "A1 F1 I1"])


class TestThePythonFormAgrees(OrderingTestCase):
    """`sample_sort_key` is used where a list is already in memory -- the CSV export's columns
    and the editor's history tally. Sorting a display string instead is what it replaced, and
    that got both the numbers and, wherever an isolate description is set, the field wrong."""

    def build(self):
        for ale, flask, isolate in [(2, 2, 2), (1, 10, 1), (1, 2, 10), (1, 2, 2), (10, 1, 1)]:
            self.make(ale, flask, isolate)

    def test_it_matches_the_database_ordering(self):
        self.build()
        in_memory = sorted(
            ResequencingExperiment.objects.filter(
                **{paths.to_experiment(): self.experiment}),
            key=sample_sort_key)
        self.assertEqual([r.sample_name for r in in_memory], self.order())

    def test_it_beats_sorting_the_display_string(self):
        """The bug this replaced, stated as a test."""
        self.build()
        by_label = sorted(
            ResequencingExperiment.objects.filter(
                **{paths.to_experiment(): self.experiment}),
            key=lambda r: r.ale_flask_isolate_str)
        self.assertNotEqual([r.sample_name for r in by_label], self.order())

"""The experiment frequency cutoff, which for a long time excluded nothing.

Two independent faults, both in one block of `filtered_mutation_call_queryset`, and
neither had a test. The queryset builds a `Q` and hands it to `.exclude()`, so every term in
it describes something to *hide*.

1. A `frequency_gatk__lt` term was ANDed in whenever `min_gatk_cutoff` was set -- which was
   always, since it defaulted to 20 and no form exposed it. No import path has ever written
   `frequency_gatk`, and a comparison against null is never true, so the AND could not be
   satisfied and **nothing was ever excluded**.
2. The floor and the ceiling were ANDed together, which reads "below the floor *and* above
   the ceiling at the same time". No row can be both, so setting a maximum silently turned
   the minimum off as well.

Between them the cutoff was inert in every configuration a person could reach through the UI.
"""

from decimal import Decimal

from aledb_filter.util import filter_mutation_calls
from aledb_filter.view_filter import ViewFilter
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import MutationCall


class FrequencyCutoffTestCase(EditorTestCase):

    def setUp(self):
        super().setUp()
        # The cutoffs are the reader's now rather than a stored row, so each test states the
        # one it is exercising. 1% and 99%: outside a cutoff at either end.
        self.view_filter = ViewFilter.parse(min_freq=20)
        self.low = self._at(self.mut_2, "0.0100")
        self.high = self._at(self.mut_3, "0.9900")

    def _at(self, mutation, frequency):
        call = MutationCall.objects.get(sample=self.sample_a,
                                                mutation=mutation)
        call.frequency = Decimal(frequency)
        call.save()
        return call

    def _kept(self):
        return {call.id for call in filter_mutation_calls(
            MutationCall.objects.all(), view_filter=self.view_filter)}

    def _cutoffs(self, **values):
        self.view_filter = ViewFilter.parse(
            min_freq=values.get("min_cutoff", self.view_filter.min_freq),
            max_freq=values.get("max_cutoff", self.view_filter.max_freq))

    # --- the floor ------------------------------------------------------------------------

    def test_a_mutation_below_the_floor_is_excluded(self):
        """The regression. With the default 20% floor this returned the 1% call,
        because a null `frequency_gatk` made the exclusion unsatisfiable."""
        self.assertNotIn(self.low.id, self._kept())

    def test_a_mutation_above_the_floor_is_kept(self):
        self.assertIn(self.high.id, self._kept())

    # --- the ceiling ----------------------------------------------------------------------

    def test_a_mutation_above_the_ceiling_is_excluded(self):
        self._cutoffs(min_cutoff=0, max_cutoff=90)

        self.assertNotIn(self.high.id, self._kept())

    # --- and both at once, which is what the AND broke -------------------------------------

    def test_both_ends_filter_when_both_are_set(self):
        """The second fault on its own. ANDed, "below 20 and above 90" is unsatisfiable, so
        setting a ceiling used to switch the floor off too and neither end did anything."""
        self._cutoffs(min_cutoff=20, max_cutoff=90)

        kept = self._kept()
        self.assertNotIn(self.low.id, kept, "1% is below the 20% floor")
        self.assertNotIn(self.high.id, kept, "99% is above the 90% ceiling")

    def test_what_lies_between_survives_both(self):
        """The other half: an exclusion that hid everything would pass the two above."""
        self._cutoffs(min_cutoff=20, max_cutoff=90)

        middle = MutationCall.objects.get(sample=self.sample_a,
                                              mutation=self.mut_1)
        self.assertEqual(Decimal("0.7500"), middle.frequency)
        self.assertIn(middle.id, self._kept())

    # --- no cutoff at either end ----------------------------------------------------------

    def test_a_filter_with_no_cutoff_hides_nothing(self):
        """`q_exp` is empty then, and an empty Q handed to `.exclude()` alongside the bare
        experiment match would hide the whole experiment. Guarded, and this is the guard."""
        self._cutoffs(min_cutoff=0, max_cutoff=100)

        kept = self._kept()
        self.assertIn(self.low.id, kept)
        self.assertIn(self.high.id, kept)

    # --- the editor is deliberately not filtered ------------------------------------------

    def test_the_editor_still_shows_what_the_cutoff_hides(self):
        """Now that the cutoff genuinely excludes, this matters more than it did: a mutation
        hidden from every table has to stay visible where it can be removed, or it cannot be
        curated and returns the moment somebody widens the filter."""
        response = self.client.get("/mutation-editor/", {
            "experiment_id": self.experiment.id, "sample_id": "all"})

        self.assertIn('data-obs="%d"' % self.low.id, response.content.decode("utf-8"))

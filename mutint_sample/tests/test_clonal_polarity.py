"""Both polarities of `Sample.is_clonal`, at every site that reads it.

The column was `is_population` and its meaning is inverted, which is the one kind of change
that shows wrong data and raises nothing. Deleting the old column made every *site* a hard
error the compiler could enumerate; it could not check a single *polarity*, because
`is_clonal=True` where `is_clonal=False` was meant compiles perfectly and reads plausibly.

So this file is the other half. One clonal sample and one mixed sample, and every read site
asserted **both ways round** -- a test that only checked the clonal case would pass unchanged
under a global re-inversion, which is exactly the mistake to catch.

Deliberately one file rather than an assertion added to each app's own tests: what needs
reviewing is the list, and a reader checking that no read site is missing should not have to
find seven of them.
"""

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from mutint_common.constants import SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED
from mutint_experiment import paths
from mutint_experiment.models import Experiment, Population, Project
from mutint_sample.breseq_report import is_mixed
from mutint_sample.models import Sample
from mutint_sample.util import get_ordered_reseq_queryset


class ClonalPolarityTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

        population = Population.objects.create(experiment=self.experiment, name="1")
        self.clone = Sample.objects.create(
            population=population, time_point=500, name="1", is_clonal=True, source_name="clone")
        self.mixed = Sample.objects.create(
            population=population, time_point=500, name="2", is_clonal=False, source_name="mixed")

    # --- the column and its property ------------------------------------------------------

    def test_the_property_is_the_negation_of_the_column(self):
        self.assertTrue(self.clone.is_clonal)
        self.assertFalse(self.clone.is_mixed)
        self.assertFalse(self.mixed.is_clonal)
        self.assertTrue(self.mixed.is_mixed)

    def test_a_sample_is_clonal_unless_something_says_otherwise(self):
        """The default. Only breseq's `-p` sets it the other way."""
        self.assertTrue(Sample().is_clonal)

    # --- the queryset helpers -------------------------------------------------------------

    def test_the_two_filters_select_opposite_sets(self):
        self.assertEqual([self.clone.pk],
                         list(Sample.objects.filter(**paths.clonal_filter())
                              .values_list("pk", flat=True)))
        self.assertEqual([self.mixed.pk],
                         list(Sample.objects.filter(**paths.mixed_filter())
                              .values_list("pk", flat=True)))

    def test_the_filters_work_from_an_call_too(self):
        """`prefix` is what a queryset that starts further back has to pass."""
        self.assertEqual({paths.FROM_CALL + "__is_clonal": True},
                         paths.clonal_filter(paths.FROM_CALL))
        self.assertEqual({paths.FROM_CALL + "__is_clonal": False},
                         paths.mixed_filter(paths.FROM_CALL))

    # --- `?sample_type=` ------------------------------------------------------------------

    def _selected(self, sample_type):
        return set(get_ordered_reseq_queryset(
            self.experiment.id, sample_type=sample_type).values_list("pk", flat=True))

    def test_the_sample_type_filter_selects_each_and_both(self):
        self.assertEqual({self.clone.pk}, self._selected(SAMPLE_TYPE_CLONAL))
        self.assertEqual({self.mixed.pk}, self._selected(SAMPLE_TYPE_MIXED))
        self.assertEqual({self.clone.pk, self.mixed.pk}, self._selected(None))

    # --- the Freq column ------------------------------------------------------------------

    def test_only_a_mixed_sample_gets_a_frequency_column(self):
        """`is_mixed` decides it for the samples page and the browser page alike, which is
        why it lives in `breseq_report` rather than on either view."""
        self.assertFalse(is_mixed(self.clone))
        self.assertTrue(is_mixed(self.mixed))
        self.assertFalse(is_mixed(None), "a sample that is not there has no Freq column")

    def test_the_freq_header_appears_for_one_and_not_the_other(self):
        for sample, expected in ((self.clone, False), (self.mixed, True)):
            with self.subTest(sample=sample.source_name):
                response = self.client.get(
                    "/mutations/breseq?experiment_id=%d&sample_id=%d"
                    % (self.experiment.id, sample.pk), follow=True)
                self.assertEqual(200, response.status_code)
                body = response.content.decode()
                self.assertEqual(expected, 'class="breseq-freq"' in body)

    # --- the word a person reads ----------------------------------------------------------

    def test_the_interop_payload_says_the_right_word_for_each(self):
        """It was `/metadata`'s builder, shared with the interop API. That page and its app
        are gone; the API is the one consumer left and owns the builder now."""
        from mutint_interop_query.views import _sample_info_list

        words = {row["label"]: row["sample_type"]
                 for row in _sample_info_list(Sample.objects.order_by("name"))}
        self.assertEqual(SAMPLE_TYPE_CLONAL, words[self.clone.label])
        self.assertEqual(SAMPLE_TYPE_MIXED, words[self.mixed.label])

    # --- the checkbox ---------------------------------------------------------------------

    def test_the_checkbox_is_ticked_for_the_mixed_sample_only(self):
        for sample, expected in ((self.clone, False), (self.mixed, True)):
            with self.subTest(sample=sample.source_name):
                body = self.client.get(
                    "/sample/%d/edit/" % sample.pk).content.decode()
                self.assertIn('id="se-mixed"', body)
                # The box is ticked by `checked` appearing inside that input's tag.
                tag = body.split('id="se-mixed"', 1)[1].split(">", 1)[0]
                self.assertEqual(expected, "checked" in tag)

    def test_ticking_the_box_makes_the_sample_mixed(self):
        response = self.client.post(
            "/sample/%d/update/" % self.clone.pk,
            {"sample_name": "clone", "ale": "1", "flask": 500, "isolate": "1",
             "isolate_description": "", "medium_description": "", "rep_tags": "",
             "is_mixed": "1"})

        self.assertEqual(200, response.status_code, response.content)
        self.clone.refresh_from_db()
        self.assertTrue(self.clone.is_mixed)

    def test_clearing_the_box_makes_it_clonal_again(self):
        """The other direction, because a form that can only set one way is half a form --
        and because an unchecked box posts `0` rather than omitting the key."""
        response = self.client.post(
            "/sample/%d/update/" % self.mixed.pk,
            {"sample_name": "mixed", "ale": "1", "flask": 500, "isolate": "2",
             "isolate_description": "", "medium_description": "", "rep_tags": "",
             "is_mixed": "0"})

        self.assertEqual(200, response.status_code, response.content)
        self.mixed.refresh_from_db()
        self.assertTrue(self.mixed.is_clonal)

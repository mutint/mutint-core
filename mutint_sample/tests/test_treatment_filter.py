"""`?treatment=` narrows samples to one treatment label, beside `?population=`.

The treatment is a label on the sample (`Sample.treatment`), and every selector that narrows
by population narrows by it too, through the one `treatment=` keyword on
`get_ordered_sample_queryset`. What is pinned here is the parsing, the filter, the list a
picker offers, and that the per-sample Mutations page honours it and carries it on.
"""

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from mutint_common.constants import REQUEST_ALL, REQUEST_TREATMENT
from mutint_experiment.models import Experiment, Population
from mutint_sample.models import Sample
from mutint_sample.util import get_ordered_sample_queryset
from mutint_sample.views.common import get_treatment, get_treatment_names


class TreatmentParsingTestCase(TestCase):

    def parse(self, query):
        return get_treatment(RequestFactory().get("/x" + query))

    def test_a_value_passes_through_as_text(self):
        self.assertEqual("glucose", self.parse("?treatment=glucose"))
        self.assertEqual("37 C", self.parse("?treatment=37%20C"))

    def test_absent_empty_and_all_mean_no_filter(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("?treatment="))
        self.assertIsNone(self.parse("?%s=%s" % (REQUEST_TREATMENT, REQUEST_ALL)))

    def test_the_vocabulary_names_it(self):
        from mutint_common.context_processors import request_vocabulary
        self.assertEqual(REQUEST_TREATMENT,
                         request_vocabulary(RequestFactory().get("/"))["PARAM_TREATMENT"])


class TreatmentFilterTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])
        one = Population.objects.create(experiment=self.experiment, name="1")
        two = Population.objects.create(experiment=self.experiment, name="2")
        self.glucose = self._sample(one, 100, "glucose")
        self.lactose = self._sample(one, 200, "lactose")
        self.untreated = self._sample(two, 100, None)
        self.blank = self._sample(two, 200, "")
        self.ancestor = self._sample(two, 0, "ancestral")
        self.experiment.set_ancestor(self.ancestor, self.user)

    def _sample(self, population, time_point, treatment):
        return Sample.objects.create(population=population, time_point=time_point, name="1",
                                     is_clonal=True, treatment=treatment,
                                     source_name="%s-%s" % (population.name, time_point))

    def selected(self, **kwargs):
        return set(get_ordered_sample_queryset(self.experiment.id, **kwargs)
                   .values_list("pk", flat=True))

    def test_a_treatment_selects_the_samples_carrying_it(self):
        self.assertEqual({self.glucose.pk}, self.selected(treatment="glucose"))
        self.assertEqual({self.lactose.pk}, self.selected(population="1", treatment="lactose"))
        self.assertEqual(set(), self.selected(population="2", treatment="lactose"))

    def test_no_treatment_selects_every_sample(self):
        everything = {self.glucose.pk, self.lactose.pk, self.untreated.pk, self.blank.pk}
        self.assertEqual(everything, self.selected())
        self.assertEqual(everything, self.selected(treatment=None))
        self.assertEqual(everything, self.selected(treatment=""))

    def test_the_names_are_the_non_blank_labels_without_the_ancestors(self):
        self.assertEqual(["glucose", "lactose"], get_treatment_names(self.experiment.id))
        # Ordered as population names are, by the same zero-padding: digits by value, and
        # among words a shorter one before a longer, so "ancestral" follows the seven-letter
        # two.
        self.assertEqual(["glucose", "lactose", "ancestral"],
                         get_treatment_names(self.experiment.id, include_ancestor=True))
        Sample.objects.filter(pk__in=[self.glucose.pk, self.lactose.pk]).update(treatment="")
        self.assertEqual([], get_treatment_names(self.experiment.id))

    def test_the_mutations_page_narrows_its_picker_and_carries_the_treatment(self):
        response = self.client.get(
            "/mutations/breseq?experiment_id=%d&treatment=glucose&population=1"
            % self.experiment.id)
        self.assertEqual(200, response.status_code)
        body = response.content.decode()
        self.assertIn("sample_id=%d&amp;population=1&amp;treatment=glucose" % self.glucose.pk,
                       body)
        self.assertNotIn("sample_id=%d&amp;" % self.lactose.pk, body)

    def test_the_overview_shows_a_treatment_column_only_when_there_is_one(self):
        response = self.client.get("/stats?experiment_id=%d" % self.experiment.id, follow=True)
        self.assertEqual(200, response.status_code)
        body = response.content.decode()
        self.assertIn("<th>Treatment</th>", body)
        self.assertIn('<td class="treatment">glucose</td>', body)
        response = self.client.get("/stats?experiment_id=%d&treatment=lactose" % self.experiment.id,
                                   follow=True)
        body = response.content.decode()
        self.assertIn('<td class="treatment">lactose</td>', body)
        self.assertNotIn('<td class="treatment">glucose</td>', body)

        Sample.objects.filter(population__experiment=self.experiment).update(treatment="")
        body = self.client.get("/stats?experiment_id=%d" % self.experiment.id,
                               follow=True).content.decode()
        self.assertNotIn("<th>Treatment</th>", body)

    def test_the_sample_box_names_the_treatment(self):
        body = self.client.get(
            "/mutations/breseq?experiment_id=%d&sample_id=%d"
            % (self.experiment.id, self.glucose.pk)).content.decode()
        self.assertIn("treatment", body)
        self.assertIn("glucose", body)

"""The experiment's treatments: listed and renamed on its edit page.

A treatment is a label on the sample, so the experiment's list of them is its samples'
distinct values. The edit page is where that list is maintained -- a rename reaches every
sample carrying the label -- and where a population whose samples disagree is named.
"""

from django.contrib.auth.models import User

from mutint_experiment.permissions import grant_project_access
from mutint_experiment.roles import ROLE_READ
from mutint_experiment.tests.test_sample_edit import SampleEditTestCase
from mutint_experiment import treatments
from mutint_sample.models import Sample


class TreatmentsTestCase(SampleEditTestCase):

    def setUp(self):
        super().setUp()
        self.a = self.make_sample("1", 100, "a", source_name="a")
        self.b = self.make_sample("1", 200, "b", source_name="b")
        self.c = self.make_sample("2", 100, "c", source_name="c")
        self.d = self.make_sample("2", 200, "d", source_name="d")
        for sample, treatment in ((self.a, "glucose"), (self.b, "glucose"), (self.c, "lactose")):
            sample.treatment = treatment
            sample.save(update_fields=["treatment"])

    def rename(self, old, new, experiment=None):
        experiment = experiment or self.experiment
        return self.client.post("/experiment/%d/treatments/rename/" % experiment.id,
                                {"old": old, "new": new})

    def treatments_of(self, *samples):
        return [Sample.objects.get(pk=s.pk).treatment for s in samples]

    def test_the_list_is_the_samples_labels_counted(self):
        self.assertEqual([{"name": "glucose", "count": 2}, {"name": "lactose", "count": 1}],
                         treatments.treatments_in_use(self.experiment))

    def test_the_list_sorts_numbers_by_value(self):
        for sample, treatment in ((self.a, "10 mM"), (self.b, "2 mM"), (self.c, "2 mM")):
            sample.treatment = treatment
            sample.save(update_fields=["treatment"])
        self.assertEqual(["2 mM", "10 mM"],
                         [e["name"] for e in treatments.treatments_in_use(self.experiment)])

    def test_a_population_holding_two_treatments_is_named(self):
        # Population 2 holds lactose and a sample with none; population 1 is uniform.
        self.assertEqual([{"population": "2", "treatments": ["", "lactose"]}],
                         treatments.mixed_populations(self.experiment))
        self.d.treatment = "lactose"
        self.d.save(update_fields=["treatment"])
        self.assertEqual([], treatments.mixed_populations(self.experiment))

    def test_renaming_reaches_every_sample_of_this_experiment_only(self):
        created = self.client.post(
            "/project/create/", {"name": "Q", "experiment": "F"}).json()
        from mutint_experiment.models import Experiment
        other = Experiment.objects.get(pk=created["experiment_id"])
        foreign = self.make_sample("1", 1, "f", source_name="f", experiment=other)
        foreign.treatment = "glucose"
        foreign.save(update_fields=["treatment"])

        response = self.rename("glucose", " galactose ")

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(2, response.json()["renamed"])
        self.assertEqual(["galactose", "galactose", "lactose", "glucose"],
                         self.treatments_of(self.a, self.b, self.c, foreign))

    def test_renaming_onto_a_name_in_use_merges_and_a_blank_clears(self):
        self.assertEqual(1, self.rename("lactose", "glucose").json()["renamed"])
        self.assertEqual([{"name": "glucose", "count": 3}],
                         treatments.treatments_in_use(self.experiment))
        self.assertEqual(3, self.rename("glucose", "").json()["renamed"])
        self.assertEqual([], treatments.treatments_in_use(self.experiment))

    def test_a_rename_is_refused_when_malformed_or_too_long(self):
        self.assertEqual(400, self.rename("", "x").status_code)
        self.assertEqual(400, self.rename("glucose", "x" * 101).status_code)
        self.assertEqual(["glucose"], self.treatments_of(self.a))

    def test_a_reader_and_a_locked_experiment_are_refused(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.project, reader, ROLE_READ, granted_by=self.user)
        self.client.force_login(reader)
        self.assertEqual(403, self.rename("glucose", "x").status_code)
        self.client.force_login(self.user)
        self.experiment.lock(self.user)
        self.assertEqual(403, self.rename("glucose", "x").status_code)
        self.assertEqual(["glucose"], self.treatments_of(self.a))

    def test_the_edit_page_lists_them_and_warns_about_the_mixed_population(self):
        body = self.client.get("/experiment/%d/edit/" % self.experiment.id).content.decode()
        self.assertIn('id="ee-treatments"', body)
        self.assertIn('data-treatment="glucose"', body)
        self.assertIn('data-treatment="lactose"', body)
        self.assertIn("/treatments/rename/", body)
        self.assertIn("alert-warning", body)
        self.assertIn("<b>2</b>", body)
        self.assertNotIn("<b>1</b>", body)

    def test_an_experiment_with_no_treatments_has_no_section(self):
        self.rename("glucose", "")
        self.rename("lactose", "")
        body = self.client.get("/experiment/%d/edit/" % self.experiment.id).content.decode()
        self.assertNotIn('id="ee-treatments"', body)
        self.assertNotIn("alert-warning", body)

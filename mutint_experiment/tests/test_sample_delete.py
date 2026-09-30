"""Deleting samples from the sample page and the Edit samples table, and the ancestor button.

Both delete endpoints end in `samples.delete_samples`, so most of what is pinned here is what
a deleted sample takes with it -- and, as much, what it must leave alone: a mutation another
sample still observes, and a population another sample still sits in.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings

from mutint_common import store
from mutint_experiment.models import Experiment, Population
from mutint_experiment.permissions import grant_project_access
from mutint_experiment.roles import ROLE_READ
from mutint_experiment.tests.test_sample_edit import SampleEditTestCase
from mutint_import import import_lock
from mutint_sample.models import Mutation, MutationCall, Sample


class SampleDeleteTestCase(SampleEditTestCase):

    def setUp(self):
        super().setUp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.gone = self.make_sample("Ara-1", 500, "A", source_name="gone")
        self.kept = self.make_sample("Ara-2", 500, "B", source_name="kept")
        self.only_gone = self.mutation(100)
        self.shared = self.mutation(200)
        MutationCall.objects.create(sample=self.gone, mutation=self.only_gone, present=True)
        MutationCall.objects.create(sample=self.gone, mutation=self.shared, present=True)
        MutationCall.objects.create(sample=self.kept, mutation=self.shared, present=True)
        os.makedirs(store.sample_report_dir(self.gone.pk))

    def mutation(self, position):
        return Mutation.objects.create(
            experiment=self.experiment, start_position=position, seq_id="chr",
            mutation_type="SNP", sequence_change="A->G", gene="thrA")

    def delete_one(self, sample):
        return self.client.post("/sample/%d/delete/" % sample.pk)

    def delete_many(self, ids):
        return self.client.post("/experiment/%d/samples/delete/" % self.experiment.id,
                                {"sample_ids": json.dumps(ids)})

    # --- what goes, and what stays -------------------------------------------------------

    def test_deleting_a_sample_takes_its_calls_files_and_emptied_population(self):
        response = self.delete_one(self.gone)

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual([self.gone.label], response.json()["deleted"])
        self.assertFalse(Sample.objects.filter(pk=self.gone.pk).exists())
        self.assertFalse(MutationCall.objects.filter(sample_id=self.gone.pk).exists())
        self.assertFalse(os.path.exists(store.sample_dir(self.gone.pk)))
        self.assertFalse(Population.objects.filter(name="Ara-1").exists())

    def test_a_mutation_only_it_observed_goes_and_a_shared_one_stays(self):
        self.delete_one(self.gone)

        self.assertFalse(Mutation.objects.filter(pk=self.only_gone.pk).exists())
        self.assertTrue(Mutation.objects.filter(pk=self.shared.pk).exists())
        self.assertEqual(1, MutationCall.objects.filter(mutation=self.shared).count())

    def test_a_population_another_sample_sits_in_stays(self):
        sibling = self.make_sample("Ara-1", 1000, "C")
        self.delete_one(self.gone)
        self.assertTrue(Population.objects.filter(pk=sibling.population_id).exists())

    def test_deleting_the_ancestor_clears_the_designation(self):
        self.experiment.set_ancestor(self.gone, self.user)
        self.delete_one(self.gone)
        self.assertIsNone(Experiment.objects.get(pk=self.experiment.pk).ancestor_id)

    # --- the bulk endpoint ---------------------------------------------------------------

    def test_the_table_deletes_every_ticked_sample(self):
        response = self.delete_many([self.gone.pk, self.kept.pk])
        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(0, Sample.objects.filter(
            population__experiment=self.experiment).count())
        self.assertEqual(0, Mutation.objects.filter(experiment=self.experiment).count())

    def test_a_sample_not_in_this_experiment_deletes_nothing(self):
        other = Experiment.objects.get(pk=self.client.post(
            "/project/create/", {"name": "Q", "experiment": "F"}).json()["experiment_id"])
        stranger = self.make_sample("X", 1, "1", experiment=other)

        response = self.delete_many([self.gone.pk, stranger.pk])

        self.assertEqual(404, response.status_code)
        self.assertEqual(3, Sample.objects.count())

    def test_nothing_ticked_or_malformed_is_a_400(self):
        self.assertEqual(400, self.delete_many([]).status_code)
        response = self.client.post("/experiment/%d/samples/delete/" % self.experiment.id,
                                    {"sample_ids": "not json"})
        self.assertEqual(400, response.status_code)

    # --- refusals -----------------------------------------------------------------------

    def test_a_locked_experiment_refuses_both(self):
        self.experiment.lock(self.user)
        self.assertEqual(403, self.delete_one(self.gone).status_code)
        self.assertEqual(403, self.delete_many([self.gone.pk]).status_code)
        self.assertEqual(2, Sample.objects.count())

    def test_a_reader_cannot_delete(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.project, reader, ROLE_READ, granted_by=self.user)
        self.client.force_login(reader)
        self.assertEqual(403, self.delete_one(self.gone).status_code)
        self.assertEqual(403, self.delete_many([self.gone.pk]).status_code)
        self.assertEqual(2, Sample.objects.count())

    def test_an_import_in_progress_is_a_409_and_deletes_nothing(self):
        with mock.patch.object(import_lock, "acquire",
                               side_effect=import_lock.ImportInProgress("busy")):
            response = self.delete_one(self.gone)
        self.assertEqual(409, response.status_code)
        self.assertTrue(Sample.objects.filter(pk=self.gone.pk).exists())

    def test_it_is_a_post(self):
        self.assertEqual(405, self.client.get("/sample/%d/delete/" % self.gone.pk).status_code)

    # --- the pages ----------------------------------------------------------------------

    def test_both_pages_offer_it_behind_a_typed_confirm(self):
        for url, control, endpoint in (
                ("/sample/%d/edit/" % self.gone.pk, 'id="se-delete"',
                 "/sample/%d/delete/" % self.gone.pk),
                ("/experiment/%d/samples/" % self.experiment.id, 'id="sb-delete"',
                 "/experiment/%d/samples/delete/" % self.experiment.id)):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, control)
                self.assertContains(response, endpoint)
                self.assertContains(response, "mutintConfirmTyped(")
                # mutintConfirmTyped calls swal(), which base.html does not load.
                self.assertContains(response, "sweetalert.min.js")

    def test_every_row_of_the_table_has_a_box_to_tick(self):
        html = self.client.get("/experiment/%d/samples/" % self.experiment.id).content.decode()
        for sample in (self.gone, self.kept):
            self.assertIn('id="sb-select-%d"' % sample.pk, html)


class SampleAncestorButtonTestCase(SampleEditTestCase):
    """The sample page's Ancestor section, which posts to the experiment's own endpoint."""

    def setUp(self):
        super().setUp()
        self.sample = self.make_sample(1, 1, "1-1")
        self.other = self.make_sample(1, 2, "1-2")

    def page(self, sample):
        return self.client.get("/sample/%d/edit/" % sample.pk)

    def test_an_undesignated_sample_offers_to_become_the_ancestor(self):
        response = self.page(self.sample)
        self.assertContains(response, "Designate as ancestor")
        self.assertContains(response, 'data-sample-id="%d"' % self.sample.pk)
        self.assertContains(response, "/experiment/%d/ancestor/apply/" % self.experiment.id)

    def test_the_ancestor_offers_to_clear_it(self):
        self.experiment.set_ancestor(self.sample, self.user)
        response = self.page(self.sample)
        self.assertContains(response, "Remove ancestor designation")
        self.assertContains(response, 'data-sample-id=""')

    def test_another_sample_being_the_ancestor_is_named(self):
        self.experiment.set_ancestor(self.other, self.user)
        response = self.page(self.sample)
        self.assertContains(response, "This replaces <b>%s</b>" % self.other.label)

    def test_the_table_offers_it_and_marks_the_current_ancestor(self):
        url = "/experiment/%d/samples/" % self.experiment.id
        response = self.client.get(url)
        self.assertContains(response, 'id="sb-ancestor"')
        self.assertContains(response, "/experiment/%d/ancestor/apply/" % self.experiment.id)
        self.assertNotContains(response, 'class="label label-danger sb-ancestor-badge"')

        self.experiment.set_ancestor(self.other, self.user)
        html = self.client.get(url).content.decode()
        self.assertIn('data-ancestor-id="%d"' % self.other.pk, html)
        row = html[html.index('<tr data-sample-id="%d"' % self.other.pk):]
        self.assertIn("sb-ancestor-badge", row[:row.index("</tr>")])
        row = html[html.index('<tr data-sample-id="%d"' % self.sample.pk):]
        self.assertNotIn("sb-ancestor-badge", row[:row.index("</tr>")])

"""The clear endpoints, the Storage panel, the project block and the table columns."""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_common.storage_registry import rebuild_storage
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import grant_project_access
from mutint_experiment.roles import ROLE_READ
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample import storage
from mutint_sample.models import Sample


class StorageViewTestCase(TestCase):

    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.owner = User.objects.create(username="own", email="o@e.com", is_active=True)
        self.reader = User.objects.create(username="rd", email="r@e.com", is_active=True)

        breseq_fixture.write_sample(self.drop, "s1", bam_bytes=b"B" * 2000)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="own")
        self.sample = Sample.objects.get()
        self.experiment = self.sample.experiment
        self.project = self.experiment.project
        self.project.user = self.owner
        self.project.save()
        grant_project_access(self.project, self.reader, ROLE_READ)
        rebuild_storage(self.experiment.id)

    def clear_url(self):
        return "/experiment/%d/storage/clear/" % self.experiment.id

    def bam_exists(self):
        return os.path.exists(store.sample_path(self.sample.id, store.SAMPLE_BAM))


class ExperimentEndpointTestCase(StorageViewTestCase):

    def test_anonymous_is_refused(self):
        response = self.client.post(self.clear_url(), {"kind": storage.ALIGNMENTS})
        self.assertEqual(403, response.status_code)
        self.assertTrue(self.bam_exists())

    def test_a_reader_is_refused(self):
        self.client.force_login(self.reader)
        response = self.client.post(self.clear_url(), {"kind": storage.ALIGNMENTS})
        self.assertEqual(403, response.status_code)
        self.assertTrue(self.bam_exists())

    def test_a_locked_experiment_refuses_and_says_why(self):
        self.experiment.lock(self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(self.clear_url(), {"kind": storage.ALIGNMENTS})
        self.assertEqual(403, response.status_code)
        self.assertIn("locked", response.json()["error"])
        self.assertTrue(self.bam_exists())

    def test_an_unknown_kind_is_a_400(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.clear_url(), {"kind": "nope"})
        self.assertEqual(400, response.status_code)

    def test_a_measured_only_kind_is_a_409(self):
        from django.apps import apps
        from mutint_common.storage_registry import (
            register_storage_kind, unregister_storage_kind,
        )
        register_storage_kind(apps.get_app_config("mutint_common"), key="t_ro",
                              label="RO", measure=lambda e: 1)
        self.addCleanup(unregister_storage_kind, "t_ro")
        self.client.force_login(self.owner)
        response = self.client.post(self.clear_url(), {"kind": "t_ro"})
        self.assertEqual(409, response.status_code)
        self.assertIn("RO", response.json()["error"])

    def test_the_owner_clears_and_the_files_go(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.clear_url(), {"kind": storage.ALIGNMENTS})
        self.assertEqual(200, response.status_code, response.content)
        body = response.json()
        self.assertGreaterEqual(body["freed_bytes"], 2000)
        self.assertFalse(self.bam_exists())
        mine = {u["key"]: u["bytes"] for u in body["usage"]}
        self.assertEqual(0, mine[storage.ALIGNMENTS])

    def test_it_refuses_a_GET(self):
        self.client.force_login(self.owner)
        self.assertEqual(405, self.client.get(self.clear_url()).status_code)


class ProjectEndpointTestCase(StorageViewTestCase):

    def setUp(self):
        super().setUp()
        # A second experiment in the same project, with its own stored sample.
        drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, drop, True)
        breseq_fixture.write_sample(drop, "s2", bam_bytes=b"C" * 3000)
        self.second = Experiment.objects.create(name="f", project=self.project)
        breseq_folder.import_samples_into(self.second, drop)
        self.second_sample = Sample.objects.exclude(pk=self.sample.pk).get()
        rebuild_storage(self.second.id)

    def url(self):
        return "/project/%d/storage/clear/" % self.project.id

    def test_a_reader_is_refused(self):
        self.client.force_login(self.reader)
        self.assertEqual(403, self.client.post(
            self.url(), {"kind": storage.ALIGNMENTS}).status_code)

    def test_a_locked_experiment_is_skipped_and_its_sibling_cleared(self):
        self.experiment.lock(self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(self.url(), {"kind": storage.ALIGNMENTS})
        self.assertEqual(200, response.status_code, response.content)
        body = response.json()
        self.assertEqual(["e"], body["skipped"])
        self.assertEqual(["f"], body["cleared"])
        self.assertTrue(self.bam_exists())
        self.assertFalse(os.path.exists(
            store.sample_path(self.second_sample.id, store.SAMPLE_BAM)))
        self.assertGreaterEqual(body["freed_bytes"], 3000)

    def test_an_unknown_kind_is_refused_before_anything_is_touched(self):
        self.client.force_login(self.owner)
        self.assertEqual(400, self.client.post(self.url(), {"kind": "nope"}).status_code)
        self.assertTrue(self.bam_exists())


class PanelTestCase(StorageViewTestCase):

    def stats(self):
        return self.client.get("/stats?experiment_id=%d" % self.experiment.id, follow=True)

    def test_the_panel_is_on_the_overview_with_a_clear_button_for_an_editor(self):
        self.client.force_login(self.owner)
        html = self.stats().content.decode()
        self.assertIn('id="storage-table"', html)
        self.assertIn("Alignments and coverage", html)
        self.assertIn('class="btn btn-danger btn-xs storage-clear"', html)
        # The script is inside the panel body, which the page renders inside its content
        # block; a script outside a block is discarded silently, hence asserting it.
        self.assertIn("mutintConfirmTypedClear", html)

    def test_a_reader_sees_sizes_and_no_button(self):
        self.client.force_login(self.reader)
        html = self.stats().content.decode()
        self.assertIn('id="storage-table"', html)
        self.assertNotIn("storage-clear", html)

    def test_a_locked_experiment_offers_no_button_to_its_owner(self):
        self.experiment.lock(self.owner)
        self.client.force_login(self.owner)
        html = self.stats().content.decode()
        self.assertIn('id="storage-table"', html)
        self.assertNotIn("btn-xs storage-clear", html)

    def test_the_panel_measures_a_stale_experiment(self):
        from mutint_common.models import StorageUsage
        from mutint_common.storage_registry import request_remeasure
        StorageUsage.objects.filter(experiment=self.experiment).delete()
        request_remeasure(self.experiment.id)
        self.client.force_login(self.owner)
        self.stats()
        self.assertTrue(StorageUsage.objects.filter(
            experiment=self.experiment, kind=storage.ALIGNMENTS, bytes__gte=2000).exists())


class ProjectPageTestCase(StorageViewTestCase):

    def test_the_project_page_has_the_block_and_the_column(self):
        self.client.force_login(self.owner)
        html = self.client.get("/project/%d/" % self.project.id).content.decode()
        self.assertIn('id="storage-table"', html)
        self.assertIn("Clear in every experiment", html)
        self.assertIn("<th>Stored data</th>", html)

    def test_a_reader_gets_the_sizes_and_no_buttons(self):
        self.client.force_login(self.reader)
        html = self.client.get("/project/%d/" % self.project.id).content.decode()
        self.assertIn('id="storage-table"', html)
        # The script that binds the buttons is on the page either way; the buttons are not.
        self.assertNotIn("btn-xs storage-clear", html)


class ColumnTestCase(StorageViewTestCase):
    """The column is appended last, so the DataTables indexes stay where they were."""

    def _last_header(self, html, table_id):
        head = html.split('id="%s"' % table_id, 1)[1].split("</thead>", 1)[0]
        headers = [h.strip() for h in head.split("<th") if "</th>" in h]
        return headers[-1]

    def test_every_table_ends_in_the_column_and_shows_the_size(self):
        self.client.force_login(self.owner)
        for path, table in (("/experiment/", "exp_table"),
                            ("/project/%d/" % self.project.id, "exp_table"),
                            ("/project/", "project_table")):
            with self.subTest(page=path):
                html = self.client.get(path).content.decode()
                self.assertIn("Stored data", self._last_header(html, table))
                self.assertIn("KB", html)

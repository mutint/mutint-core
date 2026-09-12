"""The dashboard's Stored Data panel: totals, awaiting purge, unattributed."""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_dashboard.models import InstallationCounts
from mutint_dashboard.util import counts, rebuild_storage_unattributed
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample


class DashboardStorageTestCase(TestCase):

    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.user = User.objects.create(username="dash", email="d@e.com", is_active=True)
        breseq_fixture.write_sample(self.drop, "s1", bam_bytes=b"B" * 4096)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="dash")
        self.experiment = Sample.objects.get().experiment
        self.client.force_login(self.user)

    def test_the_total_counts_the_imported_alignment(self):
        html = self.client.get("/dashboard").content.decode()
        self.assertIn('id="storage-panel"', html)
        self.assertIn("Total in live experiments", html)
        self.assertIn("KB", html)

    def test_a_deleted_experiment_moves_to_awaiting_purge(self):
        self.client.get("/dashboard")
        self.experiment.soft_delete(self.user)
        from mutint_dashboard.views import storage_context
        context = storage_context()
        self.assertGreaterEqual(context["storage_awaiting_purge"], 4096)
        self.assertEqual(0, context["storage_total"])

    def test_orphans_and_staging_are_unattributed(self):
        os.makedirs(os.path.join(self.store, "samples", "999"))
        with open(os.path.join(self.store, "samples", "999", "aligned.bam"), "wb") as f:
            f.write(b"o" * 100)
        os.makedirs(os.path.join(self.store, "staging", "abc"))
        with open(os.path.join(self.store, "staging", "abc", "x"), "wb") as f:
            f.write(b"s" * 50)
        rebuild_storage_unattributed()
        row = counts(InstallationCounts.STORAGE_UNATTRIBUTED)
        self.assertEqual(100, row["orphan_samples"])
        self.assertEqual(50, row["staging"])
        self.assertEqual(0, row["orphan_experiments"])
        self.assertEqual(150, row["total"])

    def test_a_missing_store_is_zero(self):
        with override_settings(MUTINT_STORE_DIR=os.path.join(self.store, "nowhere")):
            rebuild_storage_unattributed()
        self.assertEqual(0, counts(InstallationCounts.STORAGE_UNATTRIBUTED)["total"])

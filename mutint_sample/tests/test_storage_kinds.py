"""The two kinds core registers: alignments (BAM, index, BigWig as one unit) and the report.

Built on a real stored sample so the measures are checked against files, not flags.
"""

import os
import shutil
import tempfile
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_common.storage_registry import clear_kind, get_storage_kind, is_clearable
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample import storage
from mutint_sample.models import Sample


class StoredSampleTestCase(TestCase):

    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.user = User.objects.create(username="kinds", email="k@e.com", is_active=True)

        breseq_fixture.write_sample(self.drop, "s1", bam_bytes=b"B" * 1000)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="kinds")
        self.sample = Sample.objects.get()
        self.experiment = self.sample.experiment
        self.experiment.project.user = self.user
        self.experiment.project.save()

        # A coverage track and a report tree beside what the import stored.
        with open(store.sample_path(self.sample.id, store.SAMPLE_BIGWIG), "wb") as handle:
            handle.write(b"W" * 300)
        report = store.sample_report_dir(self.sample.id)
        os.makedirs(os.path.join(report, "evidence"))
        with open(os.path.join(report, "index.html"), "wb") as handle:
            handle.write(b"<html>" * 100)
        with open(os.path.join(report, "evidence", "RA_1.html"), "wb") as handle:
            handle.write(b"x" * 44)
        self.sample.coverage_stored = True
        self.sample.report_stored = True
        self.sample.save()

    def _bytes(self, name):
        return os.path.getsize(store.sample_path(self.sample.id, name))


class MeasureTestCase(StoredSampleTestCase):

    def test_both_kinds_are_registered_and_clearable(self):
        for key in (storage.ALIGNMENTS, storage.REPORT):
            self.assertEqual("mutint_sample", get_storage_kind(key)["app"])
            self.assertTrue(is_clearable(key))

    def test_alignments_are_the_three_files_added_up(self):
        expected = sum(self._bytes(n) for n in
                       (store.SAMPLE_BAM, store.SAMPLE_BAI, store.SAMPLE_BIGWIG))
        self.assertEqual(1000 + 300 + os.path.getsize(
            store.sample_path(self.sample.id, store.SAMPLE_BAI)), expected)
        self.assertEqual(expected, storage.measure_alignments(self.experiment))

    def test_the_report_is_the_tree_added_up(self):
        self.assertEqual(600 + 44, storage.measure_report(self.experiment))

    def test_the_gd_and_the_reference_are_never_counted(self):
        total = storage.measure_alignments(self.experiment) + storage.measure_report(
            self.experiment)
        gd = os.path.getsize(store.sample_path(self.sample.id, store.SAMPLE_GD))
        self.assertGreater(gd, 0)
        everything = sum(os.path.getsize(os.path.join(root, f))
                         for root, _, files in os.walk(self.store) for f in files)
        self.assertLess(total, everything)


class ClearTestCase(StoredSampleTestCase):

    def test_clearing_alignments_removes_all_three_and_flips_both_flags(self):
        freed = clear_kind(self.experiment, storage.ALIGNMENTS)
        self.assertGreater(freed, 1000)
        for name in (store.SAMPLE_BAM, store.SAMPLE_BAI, store.SAMPLE_BIGWIG):
            self.assertFalse(os.path.exists(store.sample_path(self.sample.id, name)), name)
        self.sample.refresh_from_db()
        self.assertFalse(self.sample.bam_stored)
        self.assertFalse(self.sample.coverage_stored)
        # The data of record stays.
        self.assertTrue(os.path.exists(store.sample_path(self.sample.id, store.SAMPLE_GD)))
        self.assertTrue(os.path.exists(store.experiment_reference_path(
            self.experiment.id, store.REFERENCE_FASTA)))
        self.assertTrue(self.sample.report_stored)
        self.assertEqual(0, storage.measure_alignments(self.experiment))

    def test_clearing_the_report_removes_the_tree_and_the_flag(self):
        clear_kind(self.experiment, storage.REPORT)
        self.assertFalse(os.path.exists(store.sample_report_dir(self.sample.id)))
        self.sample.refresh_from_db()
        self.assertFalse(self.sample.report_stored)
        self.assertTrue(self.sample.bam_stored)

    def test_clearing_twice_is_harmless(self):
        clear_kind(self.experiment, storage.ALIGNMENTS)
        self.assertEqual(0, clear_kind(self.experiment, storage.ALIGNMENTS))

    def test_the_browser_and_the_report_stop_offering_the_sample(self):
        self.client.force_login(self.user)
        self.assertEqual(200, self.client.get(
            "/mutations/report/%d/" % self.sample.id).status_code)
        clear_kind(self.experiment, storage.ALIGNMENTS)
        clear_kind(self.experiment, storage.REPORT)
        self.assertEqual(404, self.client.get(
            "/mutations/report/%d/" % self.sample.id).status_code)
        from mutint_sample.views.browse import _sample_track
        self.sample.refresh_from_db()
        self.assertIsNone(_sample_track(self.sample))

    def test_the_coverage_backfill_has_nothing_to_do_afterwards(self):
        clear_kind(self.experiment, storage.ALIGNMENTS)
        out = StringIO()
        call_command("coverage", str(self.experiment.id), "--dry-run", stdout=out)
        self.assertNotIn("s1", out.getvalue())

"""`./mutint storage`: the shell counterpart of the Storage panel."""

import os
import shutil
import tempfile
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample


class StorageCommandTestCase(TestCase):

    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.user = User.objects.create(username="cmd", email="c@e.com", is_active=True)
        breseq_fixture.write_sample(self.drop, "s1", bam_bytes=b"B" * 2048)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="cmd")
        self.sample = Sample.objects.get()
        self.experiment = self.sample.experiment

    def run_command(self, *args):
        out = StringIO()
        call_command("storage", *args, stdout=out)
        return out.getvalue()

    def test_list_names_the_experiment_and_the_totals(self):
        out = self.run_command("--list")
        self.assertIn("e", out)
        self.assertIn("total in live experiments", out)
        self.assertIn("awaiting purge", out)
        self.assertIn("unattributed", out)
        self.assertIn("database", out)
        self.assertIn("KB", out)

    def test_one_experiment(self):
        out = self.run_command(str(self.experiment.id))
        self.assertIn("Alignments and coverage", out)
        self.assertIn("total", out)

    def test_clear_on_a_locked_experiment_proceeds_and_says_so(self):
        self.experiment.lock(self.user)
        out = self.run_command("--clear", "alignments", str(self.experiment.id))
        self.assertIn("locked", out)
        self.assertIn("freed", out)
        self.assertFalse(os.path.exists(
            store.sample_path(self.sample.id, store.SAMPLE_BAM)))

    def test_an_unknown_kind_is_refused_naming_the_registered_ones(self):
        with self.assertRaises(CommandError) as raised:
            self.run_command("--clear", "nope", str(self.experiment.id))
        self.assertIn("alignments", str(raised.exception))

    def test_no_experiment_and_no_list_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_command()

"""`register_sample_import_hook`: a plugin reads the parsed GenomeDiff after a sample lands.

Core keeps the mutations and the MC evidence out of a `.gd` and nothing else; a plugin that
wants the rest -- mutint-breseq keeps the evidence no mutation accounts for -- gets the same
parse here, after the calls are written, once per sample.
"""

import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from mutint_common import plugin_registry
from mutint_import import gd_import
from mutint_import.tests.test_gd_import import CLEAN_GD, ensure_reference
from mutint_sample.models import MutationCall, Sample


class SampleImportHookTestCase(TestCase):

    def setUp(self):
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.seen = []
        self._registered = list(plugin_registry._sample_import_hooks)
        self.addCleanup(self._restore)

    def _restore(self):
        plugin_registry._sample_import_hooks[:] = self._registered

    def _import(self, *lines):
        with open(CLEAN_GD, "rb") as handle:
            raw = handle.read().decode()
        ensure_reference("hook exp")
        gd_import.import_gd_files(
            [SimpleUploadedFile("1-500-1-1.gd",
                                "\n".join(raw.splitlines() + list(lines)).encode())],
            project_name="gd project", experiment_name="hook exp", owner_name="tester")
        return Sample.objects.get()

    def test_the_hook_gets_the_sample_and_the_parsed_document(self):
        plugin_registry.register_sample_import_hook(
            lambda sample, document: self.seen.append((sample, document)))

        sample = self._import("JC\t900\t.\tREL606\t100\t1\tREL606\t200\t-1\t0")

        self.assertEqual(1, len(self.seen))
        seen_sample, document = self.seen[0]
        self.assertEqual(sample.pk, seen_sample.pk)
        self.assertEqual(["JC"], [record.type for record in document.evidence])
        # After the calls are written: a hook may read them.
        self.assertTrue(MutationCall.objects.filter(sample=sample).exists())

    def test_a_hook_that_raises_does_not_fail_the_import(self):
        def broken(sample, document):
            raise RuntimeError("plugin bug")

        plugin_registry.register_sample_import_hook(broken)
        plugin_registry.register_sample_import_hook(
            lambda sample, document: self.seen.append(sample))

        with self.assertLogs("mutint_common.plugin_registry", level="ERROR"):
            sample = self._import()

        self.assertTrue(MutationCall.objects.filter(sample=sample).exists())
        self.assertEqual([sample.pk], [s.pk for s in self.seen], "the next hook still ran")

"""Deriving a sample's coverage BigWig.

The tools themselves are not exercised here -- the suite must not shell out to bedtools, and
what it would prove is bedtools' business anyway. What is worth pinning is everything around
them: the preconditions that produce a useful error instead of a confusing one, the chrom.sizes
derived from the reference's own index, and the contract that a sample keeps its reads when its
coverage cannot be built.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_common.tools import ToolMissing
from aledb_import import breseq_folder, coverage
from aledb_import.tests import breseq_fixture
from aledb_seq.models import ResequencingExperiment


class ChromSizesTestCase(TestCase):
    def test_it_keeps_the_first_two_columns_of_the_fai(self):
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)

        fai = os.path.join(scratch, "reference.fasta.fai")
        with open(fai, "w") as handle:
            handle.write("test_ref\t20000\t10\t70\t71\n")
            handle.write("plasmid\t5000\t28000\t70\t71\n")

        out = coverage.chrom_sizes_from_fai(fai, os.path.join(scratch, "chrom.sizes"))
        with open(out) as handle:
            self.assertEqual(handle.read(), "test_ref\t20000\nplasmid\t5000\n")

    def test_an_index_naming_nothing_is_an_error_not_an_empty_file(self):
        """bedGraphToBigWig would otherwise fail with something far less clear."""
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)

        fai = os.path.join(scratch, "reference.fasta.fai")
        open(fai, "w").close()

        with self.assertRaises(coverage.CoverageError):
            coverage.chrom_sizes_from_fai(fai, os.path.join(scratch, "chrom.sizes"))


class BuildPreconditionsTestCase(TestCase):
    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        # find_user() prompts on stdin for a person matching no User, which is an EOFError
        # under the test runner -- so the user exists before the import reaches it.
        User.objects.create(username="tester", email="t@e.com", is_active=True)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="tester")
        self.reseq = ResequencingExperiment.objects.get()

    def test_a_sample_with_no_alignment_says_so(self):
        self.reseq.bam_stored = False
        self.reseq.save(update_fields=["bam_stored"])

        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.build_for(self.reseq)
        self.assertIn("no stored alignment", str(caught.exception))

    def test_a_flag_without_the_file_behind_it_says_so(self):
        os.remove(store.sample_path(self.reseq.id, store.SAMPLE_BAM))

        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.build_for(self.reseq)
        self.assertIn("missing", str(caught.exception))

    def test_a_missing_reference_index_says_what_it_was_for(self):
        os.remove(store.experiment_reference_path(
            self.reseq.ale_experiment.ale_id, store.REFERENCE_FAI))

        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.build_for(self.reseq)
        self.assertIn("reference index", str(caught.exception))

    def test_build_quietly_reports_failure_rather_than_raising(self):
        """The import path's contract: coverage is worth having, not worth rejecting a
        sample over."""
        self.reseq.bam_stored = False
        self.reseq.save(update_fields=["bam_stored"])

        self.assertFalse(coverage.build_quietly(self.reseq))
        self.reseq.refresh_from_db()
        self.assertFalse(self.reseq.coverage_stored)

    def test_a_missing_tool_is_swallowed_by_build_quietly(self):
        with override_settings(ALEDB_TOOLS_DIR=tempfile.mkdtemp()):
            def absent(_name):
                raise ToolMissing("bedtools is not installed.")

            original = coverage.require
            coverage.require = absent
            try:
                self.assertFalse(coverage.build_quietly(self.reseq))
            finally:
                coverage.require = original


class StoreTestCase(TestCase):
    def test_the_bigwig_is_an_allowed_sample_artifact(self):
        """sample_path is a whitelist and raises on anything not listed."""
        path = store.sample_path(1, store.SAMPLE_BIGWIG)
        self.assertTrue(path.endswith("coverage.bw"))

    def test_an_unknown_artifact_is_still_refused(self):
        with self.assertRaises(ValueError):
            store.sample_path(1, "../../etc/passwd")

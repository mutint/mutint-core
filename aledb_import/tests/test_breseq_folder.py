import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_seq.models import ExperimentReference, Mutation, ResequencingExperiment

OTHER_SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_B)]


class BreseqFolderImportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User",
            email="t@e.com", is_active=True, is_staff=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)

        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _import(self, experiment="folder exp"):
        return breseq_folder.import_breseq_folders(
            self.drop, project_name="folder project",
            experiment_name=experiment, person="tester")

    # --- happy path -------------------------------------------------------------------

    def test_folder_import_stores_gd_bam_bai_and_reference(self):
        breseq_fixture.write_sample(self.drop, "Ara-1_500gen_762B")
        summary = self._import()

        self.assertEqual(len(summary["files"]), 1)
        self.assertIsNone(summary["files"][0]["error"])
        self.assertEqual(summary["files"][0]["file"], "Ara-1_500gen_762B")
        self.assertGreater(summary["total_mutations"], 0)

        reseq = ResequencingExperiment.objects.get()
        self.assertTrue(reseq.bam_stored)
        for artifact in (store.SAMPLE_GD, store.SAMPLE_BAM, store.SAMPLE_BAI):
            self.assertTrue(os.path.isfile(store.sample_path(reseq.id, artifact)),
                            "%s was not stored" % artifact)

        reference = ExperimentReference.objects.get()
        self.assertEqual(reference.total_length, len(breseq_fixture.SEQUENCE_A))
        self.assertEqual([s["id"] for s in reference.seq_ids], ["test_ref"])
        for artifact in (store.REFERENCE_GFF3, store.REFERENCE_FASTA, store.REFERENCE_FAI):
            self.assertTrue(
                os.path.isfile(
                    store.experiment_reference_path(reference.ale_experiment_id, artifact)),
                "%s was not stored" % artifact)

    def test_stored_bam_is_byte_identical(self):
        sample = breseq_fixture.write_sample(self.drop, "s1")
        self._import()

        reseq = ResequencingExperiment.objects.get()
        with open(os.path.join(sample, breseq_folder.BAM_RELATIVE_PATH), "rb") as handle:
            original = handle.read()
        with open(store.sample_path(reseq.id, store.SAMPLE_BAM), "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_samples_sharing_a_reference_both_import(self):
        breseq_fixture.write_sample(self.drop, "s1")
        breseq_fixture.write_sample(self.drop, "s2")
        summary = self._import()

        self.assertEqual([f["error"] for f in summary["files"]], [None, None])
        self.assertEqual(ResequencingExperiment.objects.count(), 2)
        self.assertEqual(ExperimentReference.objects.count(), 1)

    def test_nested_collection_folder_is_walked(self):
        """A drop may be one sample or a folder of them, at any depth."""
        breseq_fixture.write_sample(os.path.join(self.drop, "collection"), "s1")
        summary = self._import()
        self.assertEqual(summary["files"][0]["file"], "s1")
        self.assertIsNone(summary["files"][0]["error"])

    # --- rejection paths --------------------------------------------------------------

    def test_same_sequence_with_different_annotation_is_accepted(self):
        """Sequence is the invariant; annotation may differ between breseq runs."""
        breseq_fixture.write_sample(self.drop, "s1")

        # Same genome, an extra gene row -- a newer feature table, not a different reference.
        richer = breseq_fixture.gff3_text(
            [("test_ref", breseq_fixture.SEQUENCE_A)]).replace(
                "##FASTA",
                "test_ref\tbreseq\tgene\t10\t40\t.\t-\t.\tID=gene2;Name=thrB;product=kinase\n##FASTA")
        breseq_fixture.write_sample(self.drop, "s2", gff3_override=richer)

        summary = self._import()
        self.assertEqual([f["error"] for f in summary["files"]], [None, None])
        self.assertEqual(ResequencingExperiment.objects.count(), 2)
        self.assertEqual(ExperimentReference.objects.count(), 1)

    def test_annotation_is_not_rewritten_by_import_order(self):
        """A folder import must not silently redefine the experiment's annotation."""
        breseq_fixture.write_sample(self.drop, "s1")
        self._import()
        original_gff3 = ExperimentReference.objects.get().gff3_sha256

        richer = breseq_fixture.gff3_text(
            [("test_ref", breseq_fixture.SEQUENCE_A)]).replace(
                "##FASTA",
                "test_ref\tbreseq\tgene\t10\t40\t.\t-\t.\tID=gene2;Name=thrB\n##FASTA")
        breseq_fixture.write_sample(self.drop, "s2", gff3_override=richer)
        self._import()

        self.assertEqual(ExperimentReference.objects.get().gff3_sha256, original_gff3)

    def test_mismatched_reference_rejects_only_that_sample(self):
        breseq_fixture.write_sample(self.drop, "s1")
        breseq_fixture.write_sample(self.drop, "s2", sequences=OTHER_SEQUENCES)
        summary = self._import()

        results = {f["file"]: f for f in summary["files"]}
        self.assertIsNone(results["s1"]["error"])
        self.assertIn("sequence does not match", results["s2"]["error"])
        self.assertEqual(results["s2"]["mutations"], 0)

        # The batch continued, and the experiment kept exactly one reference.
        self.assertGreater(summary["total_mutations"], 0)
        self.assertEqual(ExperimentReference.objects.count(), 1)
        self.assertEqual(ResequencingExperiment.objects.count(), 1)

    def test_gff3_and_fasta_disagreement_is_an_error(self):
        breseq_fixture.write_sample(
            self.drop, "s1",
            fasta_override=breseq_fixture.fasta_text(OTHER_SEQUENCES))
        summary = self._import()

        self.assertIn("disagree", summary["files"][0]["error"])
        self.assertEqual(ExperimentReference.objects.count(), 0)
        self.assertEqual(ResequencingExperiment.objects.count(), 0)

    def test_missing_bai_is_a_per_sample_error(self):
        breseq_fixture.write_sample(self.drop, "s1", include_bai=False)
        summary = self._import()

        self.assertIn("reference.bam.bai", summary["files"][0]["error"])
        self.assertEqual(ResequencingExperiment.objects.count(), 0)

    def test_gff3_without_inline_fasta_is_an_error(self):
        breseq_fixture.write_sample(
            self.drop, "s1",
            gff3_override="##gff-version 3\ntest_ref\tbreseq\tgene\t1\t9\t.\t+\t.\tID=g1\n")
        summary = self._import()
        self.assertIn("##FASTA", summary["files"][0]["error"])

    # --- loose .gd files mixed into the same drop --------------------------------------

    def test_loose_gd_files_are_skipped_with_a_reason(self):
        """A bare .gd has no reference, so it cannot join a reference-checked experiment."""
        breseq_fixture.write_sample(self.drop, "s1")
        with open(os.path.join(self.drop, "Ara-1_500gen_762B.gd"), "w",
                  encoding="utf-8", newline="\n") as handle:
            handle.write(breseq_fixture.GD_TEXT)

        summary = self._import()
        results = {f["file"]: f for f in summary["files"]}

        self.assertIsNone(results["s1"]["error"])
        self.assertIn("reference genome", results["Ara-1_500gen_762B.gd"]["error"])
        self.assertEqual(results["Ara-1_500gen_762B.gd"]["mutations"], 0)

        # Only the breseq sample was imported; the bare .gd created nothing.
        self.assertEqual(ResequencingExperiment.objects.count(), 1)
        self.assertFalse(
            ResequencingExperiment.objects.filter(
                sample_name="Ara-1_500gen_762B").exists())

    def test_gd_inside_a_sample_folder_is_not_double_imported(self):
        """output/annotated.gd belongs to its sample, not the loose-.gd sweep."""
        breseq_fixture.write_sample(self.drop, "s1")
        summary = self._import()
        self.assertEqual(len(summary["files"]), 1)
        self.assertEqual(ResequencingExperiment.objects.count(), 1)

    # --- idempotency ------------------------------------------------------------------

    def test_reimport_is_idempotent(self):
        breseq_fixture.write_sample(self.drop, "s1")
        self._import()
        mutations = Mutation.objects.count()
        self._import()

        self.assertEqual(ResequencingExperiment.objects.count(), 1)
        self.assertEqual(ExperimentReference.objects.count(), 1)
        self.assertEqual(Mutation.objects.count(), mutations)


class BreseqGdFilenameTestCase(TestCase):
    """Which .gd a sample folder is read from.

    breseq writes output/output.gd. annotated.gd only exists if someone ran
    gdtools ANNOTATE afterwards, which this codebase no longer needs -- but
    folders produced back when it did must still import.
    """

    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True)

    def _import(self, name="exp"):
        return breseq_folder.import_breseq_folders(
            self.drop, project_name="p", experiment_name=name, person="tester")

    def test_output_gd_is_preferred(self):
        breseq_fixture.write_sample(self.drop, "1-1-1-1")
        self.assertEqual(
            os.path.join(self.drop, "1-1-1-1", "output", "output.gd"),
            breseq_folder.find_gd_file(os.path.join(self.drop, "1-1-1-1")))
        self.assertEqual(2, self._import()["total_mutations"])

    def test_annotated_gd_still_works(self):
        breseq_fixture.write_sample(
            self.drop, "1-1-1-1",
            gd_relative_path=os.path.join("output", "annotated.gd"))
        self.assertEqual(2, self._import()["total_mutations"])

    def test_output_gd_wins_when_both_are_present(self):
        sample = breseq_fixture.write_sample(self.drop, "1-1-1-1")
        legacy = os.path.join(sample, "output", "annotated.gd")
        with open(legacy, "w") as handle:
            handle.write(breseq_fixture.GD_TEXT.replace("\t100\t", "\t150\t"))

        self._import()
        self.assertTrue(Mutation.objects.filter(position=100).exists())
        self.assertFalse(Mutation.objects.filter(position=150).exists())

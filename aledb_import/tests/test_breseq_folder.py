import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_sample.models import (
    ReferenceSequences,
    Mutation,
    MutationCall,
    Sample,
)

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

        reseq = Sample.objects.get()
        self.assertTrue(reseq.bam_stored)
        for artifact in (store.SAMPLE_GD, store.SAMPLE_BAM, store.SAMPLE_BAI):
            self.assertTrue(os.path.isfile(store.sample_path(reseq.id, artifact)),
                            "%s was not stored" % artifact)

        reference = ReferenceSequences.objects.get()
        self.assertEqual(reference.total_length, len(breseq_fixture.SEQUENCE_A))
        self.assertEqual([s["id"] for s in reference.seq_ids], ["test_ref"])
        for artifact in (store.REFERENCE_GFF3, store.REFERENCE_FASTA, store.REFERENCE_FAI):
            self.assertTrue(
                os.path.isfile(
                    store.experiment_reference_path(reference.experiment_id, artifact)),
                "%s was not stored" % artifact)

    def test_stored_bam_is_byte_identical(self):
        sample = breseq_fixture.write_sample(self.drop, "s1")
        self._import()

        reseq = Sample.objects.get()
        with open(os.path.join(sample, breseq_folder.BAM_RELATIVE_PATH), "rb") as handle:
            original = handle.read()
        with open(store.sample_path(reseq.id, store.SAMPLE_BAM), "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_samples_sharing_a_reference_both_import(self):
        breseq_fixture.write_sample(self.drop, "s1")
        breseq_fixture.write_sample(self.drop, "s2")
        summary = self._import()

        self.assertEqual([f["error"] for f in summary["files"]], [None, None])
        self.assertEqual(Sample.objects.count(), 2)
        self.assertEqual(ReferenceSequences.objects.count(), 1)

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
        self.assertEqual(Sample.objects.count(), 2)
        self.assertEqual(ReferenceSequences.objects.count(), 1)

    def test_annotation_is_not_rewritten_by_import_order(self):
        """A folder import must not silently redefine the experiment's annotation."""
        breseq_fixture.write_sample(self.drop, "s1")
        self._import()
        original_gff3 = ReferenceSequences.objects.get().gff3_sha256

        richer = breseq_fixture.gff3_text(
            [("test_ref", breseq_fixture.SEQUENCE_A)]).replace(
                "##FASTA",
                "test_ref\tbreseq\tgene\t10\t40\t.\t-\t.\tID=gene2;Name=thrB\n##FASTA")
        breseq_fixture.write_sample(self.drop, "s2", gff3_override=richer)
        self._import()

        self.assertEqual(ReferenceSequences.objects.get().gff3_sha256, original_gff3)

    def test_mismatched_reference_rejects_only_that_sample(self):
        breseq_fixture.write_sample(self.drop, "s1")
        breseq_fixture.write_sample(self.drop, "s2", sequences=OTHER_SEQUENCES)
        summary = self._import()

        results = {f["file"]: f for f in summary["files"]}
        self.assertIsNone(results["s1"]["error"])
        # The message names the sequences that differ rather than two hash prefixes: under
        # sequence identity the contig name carries no weight, but it is what the person
        # holding the file recognises.
        self.assertIn("not the same set of sequences", results["s2"]["error"])
        self.assertIn("in this experiment but not in the upload", results["s2"]["error"])
        self.assertEqual(results["s2"]["mutations"], 0)

        # The batch continued, and the experiment kept exactly one reference.
        self.assertGreater(summary["total_mutations"], 0)
        self.assertEqual(ReferenceSequences.objects.count(), 1)
        self.assertEqual(Sample.objects.count(), 1)

    def test_gff3_and_fasta_disagreement_is_an_error(self):
        breseq_fixture.write_sample(
            self.drop, "s1",
            fasta_override=breseq_fixture.fasta_text(OTHER_SEQUENCES))
        summary = self._import()

        self.assertIn("disagree", summary["files"][0]["error"])
        self.assertEqual(ReferenceSequences.objects.count(), 0)
        self.assertEqual(Sample.objects.count(), 0)

    def test_missing_bai_is_a_per_sample_error(self):
        breseq_fixture.write_sample(self.drop, "s1", include_bai=False)
        summary = self._import()

        self.assertIn("reference.bam.bai", summary["files"][0]["error"])
        self.assertEqual(Sample.objects.count(), 0)

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
        self.assertEqual(Sample.objects.count(), 1)
        self.assertFalse(
            Sample.objects.filter(
                source_name="Ara-1_500gen_762B").exists())

    def test_gd_inside_a_sample_folder_is_not_double_imported(self):
        """A sample's data/output.gd belongs to it, not to the loose-.gd sweep."""
        breseq_fixture.write_sample(self.drop, "s1")
        summary = self._import()
        self.assertEqual(len(summary["files"]), 1)
        self.assertEqual(Sample.objects.count(), 1)

    # --- two folders, one name --------------------------------------------------------

    def test_a_repeated_sample_name_is_skipped_with_a_reason(self):
        """`find_sample_dirs` walks, so two folders at different depths can both be called
        `s1`. They are one sample, not two -- so the second does not join the first, it
        replaces it. Refused, and said out loud."""
        breseq_fixture.write_sample(os.path.join(self.drop, "plate-a"), "s1")
        breseq_fixture.write_sample(os.path.join(self.drop, "plate-b"), "s1")

        summary = self._import()

        self.assertEqual(len(summary["files"]), 2, "both folders must be reported")
        first, second = summary["files"]
        self.assertIsNone(first["error"])
        self.assertGreater(first["mutations"], 0)
        self.assertIn("also called", second["error"])
        self.assertIn("plate-a", second["error"], "it must say which one was kept")
        self.assertEqual(second["mutations"], 0)

        # One sample, from the first folder. Two would have been wrong; one silently
        # overwritten by the other is what this exists to prevent.
        self.assertEqual(Sample.objects.count(), 1)

    def test_the_first_folders_mutations_survive_the_duplicate(self):
        """The failure this fixes was silent and total: `_database_gd_mutations` deletes the
        sample's calls before writing its own, so the second folder took the first
        one's data with it while both were reported as imported.

        Asserted on *positions*, not on a count: the two folders carry the same number of
        calls, so counting rows cannot tell an overwrite from a correct import. Which
        mutations are there is the only thing that distinguishes them.
        """
        breseq_fixture.write_sample(os.path.join(self.drop, "plate-a"), "s1")
        # Same shape, different calls -- 100 and 200 become 120 and 220.
        breseq_fixture.write_sample(
            os.path.join(self.drop, "plate-b"), "s1",
            gd_text=breseq_fixture.GD_TEXT.replace("\t100\t", "\t120\t")
                                          .replace("\t200\t", "\t220\t"))

        self._import()

        positions = set(
            MutationCall.objects.values_list("mutation__position", flat=True))
        self.assertEqual(positions, {100, 200},
                         "the surviving sample must be plate-a's, not plate-b's")
        self.assertEqual(Sample.objects.count(), 1)

    def test_three_folders_of_a_name_skip_two(self):
        for parent in ("a", "b", "c"):
            breseq_fixture.write_sample(os.path.join(self.drop, parent), "s1")

        summary = self._import()

        errors = [f["error"] for f in summary["files"]]
        self.assertIsNone(errors[0])
        self.assertEqual(sum(1 for e in errors[1:] if e), 2)
        self.assertEqual(Sample.objects.count(), 1)

    def test_distinct_names_under_one_parent_are_both_imported(self):
        """The guardrail: nesting is not what is refused, a repeated name is."""
        breseq_fixture.write_sample(os.path.join(self.drop, "plate-a"), "s1")
        breseq_fixture.write_sample(os.path.join(self.drop, "plate-a"), "s2")

        summary = self._import()

        self.assertTrue(all(f["error"] is None for f in summary["files"]), summary["files"])
        self.assertEqual(Sample.objects.count(), 2)

    # --- re-import, across drops ------------------------------------------------------

    def test_a_reimport_reports_what_it_replaced(self):
        """Re-importing a sample is allowed and destructive -- it is how a corrected breseq
        run supersedes the one before it. What it must not be is *silent*: a drop that
        overwrote last month's calls looked identical to one that brought something new."""
        breseq_fixture.write_sample(self.drop, "s1")
        first = self._import()
        self.assertEqual(first["files"][0].get("replaced", 0), 0,
                         "nothing was there the first time")
        calls = MutationCall.objects.count()
        self.assertGreater(calls, 0)

        second = self._import()

        self.assertEqual(second["files"][0]["replaced"], calls)
        self.assertIsNone(second["files"][0]["error"], "a re-import is not a failure")
        self.assertEqual(Sample.objects.count(), 1)

    def test_the_replacement_notice_is_not_a_parse_warning(self):
        """They render under different headings and mean different things -- `warnings` is
        lines the parser could not read."""
        breseq_fixture.write_sample(self.drop, "s1")
        self._import()

        second = self._import()

        self.assertEqual(second["files"][0]["warnings"], [])

    # --- idempotency ------------------------------------------------------------------

    def test_reimport_is_idempotent(self):
        breseq_fixture.write_sample(self.drop, "s1")
        self._import()
        mutations = Mutation.objects.count()
        self._import()

        self.assertEqual(Sample.objects.count(), 1)
        self.assertEqual(ReferenceSequences.objects.count(), 1)
        self.assertEqual(Mutation.objects.count(), mutations)


class BreseqGdFilenameTestCase(TestCase):
    """Which .gd a sample folder is read from.

    data/output.gd, and nothing else. The trees being imported are curated ones
    that keep only data/ -- the calls beside the reference they were made against
    -- so output/ is not consulted. annotated.gd is not read either: it existed
    only because the importer needed gdtools ANNOTATE to have written
    gene_name/gene_product into the file, and annotation comes from the stored
    reference now.
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

    def test_the_gd_is_read_from_data(self):
        breseq_fixture.write_sample(self.drop, "1-1-1-1")
        self.assertEqual(
            os.path.join(self.drop, "1-1-1-1", "data", "output.gd"),
            breseq_folder.find_gd_file(os.path.join(self.drop, "1-1-1-1")))
        self.assertEqual(2, self._import()["total_mutations"])

    def test_a_gd_under_output_is_not_a_sample(self):
        """output/ is not consulted, so a tree with only that is not a sample folder
        -- it is reported rather than half-imported without its reference."""
        breseq_fixture.write_sample(
            self.drop, "1-1-1-1",
            gd_relative_path=os.path.join("output", "output.gd"))
        self.assertIsNone(
            breseq_folder.find_gd_file(os.path.join(self.drop, "1-1-1-1")))
        self.assertEqual(0, self._import()["total_mutations"])

"""
Annotation, end to end through a real import.

Drops a reference and a .gd on the same experiment the way the Add page does,
then checks the mutations come out carrying breseq's annotation -- rather than
only what the .gd file happened to say, which for a plain breseq output.gd is
nothing at all.
"""

import os
import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import annotation, gd_import, reference, reference_store
from aledb_seq.models import ExperimentReference, Mutation, ObservedMutation

ANNOTATE_FIXTURES = os.path.join(
    os.path.dirname(__file__), "..", "annotate", "tests", "fixtures")
SYNTHETIC_GFF3 = os.path.join(ANNOTATE_FIXTURES, "synthetic.gff3")
SYNTHETIC_GBK = os.path.join(ANNOTATE_FIXTURES, "synthetic.gbk")
SYNTHETIC_GD = os.path.join(ANNOTATE_FIXTURES, "synthetic.gd")


def _uploaded_as(path, name):
    with open(path, "rb") as handle:
        return SimpleUploadedFile(name, handle.read())


class AnnotatedImportTestCase(TestCase):
    """A .gd imported against a stored reference comes out annotated."""

    reference_path = SYNTHETIC_GFF3

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User",
            email="t@e.com", is_active=True, is_staff=True, date_joined=datetime.now())

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.experiment = self._establish_reference()
        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name="syn exp", person="tester")

    def _establish_reference(self):
        context = gd_import._prepare_experiment(
            "syn project", "syn exp", "tester", False)
        gff3_text, sequences = reference.normalize_reference(self.reference_path)
        reference_store.establish_or_check(
            context["experiment"], gff3_text, sequences)
        return context["experiment"]

    def mutation_at(self, position):
        return Mutation.objects.get(ale_experiment=self.experiment, position=position)

    def test_annotation_reads_the_one_stored_reference(self):
        # The same file the store hashes and igv.js draws -- there is no second
        # artifact kept alongside it.
        path = store.experiment_reference_path(
            self.experiment.id, store.REFERENCE_GFF3)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(
            path, reference_store.annotation_reference_path(self.experiment.id))

    def test_the_stored_reference_still_hashes_as_expected(self):
        stored = ExperimentReference.objects.get()
        expected_gff3, sequences = reference.normalize_reference(self.reference_path)
        self.assertEqual(reference_store.digest(expected_gff3), stored.gff3_sha256)
        self.assertEqual(
            reference_store.digest(reference_store.normalized_fasta_text(sequences)),
            stored.fasta_sha256)

    def test_every_mutation_was_imported(self):
        self.assertEqual(36, Mutation.objects.count())

    def test_every_mutation_was_annotated(self):
        categories = set(Mutation.objects.values_list("mutation_category", flat=True))
        self.assertNotIn(None, categories)
        self.assertNotIn("", categories)
        self.assertFalse(Mutation.objects.filter(annotation__isnull=True).exists())

    def test_coding_snp_is_fully_annotated(self):
        mutation = self.mutation_at(130)
        self.assertEqual("thrA", mutation.gene_name)
        self.assertEqual("b0001", mutation.locus_tag)
        self.assertEqual("nonsense", mutation.snp_type)
        self.assertEqual("snp_nonsense", mutation.mutation_category)
        self.assertEqual(130, mutation.start_position)
        self.assertEqual(130, mutation.end_position)

    def test_the_long_tail_lives_in_the_json_column(self):
        blob = self.mutation_at(130).annotation
        self.assertEqual("TAC", blob["codon_ref_seq"])
        self.assertEqual("TAA", blob["codon_new_seq"])
        self.assertEqual("Y", blob["aa_ref_seq"])
        self.assertEqual("*", blob["aa_new_seq"])
        self.assertEqual("11", blob["transl_table"])
        self.assertEqual("thrA", blob["genes_inactivated"])

    def test_the_json_blob_is_self_describing(self):
        # The promoted columns are a projection for querying, not a move: the blob
        # still carries them so rendering is a plain dict merge.
        blob = self.mutation_at(130).annotation
        for key in ("gene_name", "locus_tag", "snp_type", "mutation_category"):
            self.assertIn(key, blob)

    def test_protein_change_is_filled_in(self):
        # It was always "" on this path before, because a bare .gd carries no
        # annotation to derive it from.
        self.assertEqual("Y10* (TAC→TAA)", self.mutation_at(130).protein_change)

    def test_gene_and_product_come_from_the_reference(self):
        mutation = self.mutation_at(130)
        self.assertEqual("thrA", mutation.gene)
        self.assertEqual("aspartokinase I", mutation.product)

    def test_intergenic_snp(self):
        mutation = self.mutation_at(450)
        self.assertEqual("thrA/thrB", mutation.gene_name)
        self.assertEqual("intergenic", mutation.snp_type)
        self.assertEqual("intergenic (+50/+51)", mutation.annotation["gene_position"])

    def test_large_deletion_spanning_genes(self):
        mutation = self.mutation_at(90)
        self.assertEqual("large_deletion", mutation.mutation_category)
        self.assertEqual("thrA–[araJ]", mutation.gene_name)
        self.assertEqual("thrA,thrB", mutation.annotation["genes_inactivated"])
        self.assertEqual(1089, mutation.end_position)

    def test_gd_data_stays_verbatim(self):
        # to_gd_line() splats every gd_data key onto the line it emits for
        # gdtools APPLY, so annotation must not leak in there.
        gd_data = self.mutation_at(130).gd_data
        self.assertEqual("SNP", gd_data["type"])
        self.assertEqual("A", gd_data["new_seq"])
        for key in ("gene_name", "snp_type", "mutation_category", "html_mutation"):
            self.assertNotIn(key, gd_data)

    def test_the_emitted_gd_line_carries_no_annotation(self):
        line = self.mutation_at(130).to_gd_line()
        self.assertNotIn("gene_name", line)
        self.assertNotIn("html_mutation", line)

    def test_observations_are_attributed_to_breseq(self):
        self.assertEqual(36, ObservedMutation.objects.filter(source="breseq").count())


class AnnotatedFromGenbankTestCase(AnnotatedImportTestCase):
    """The same import, with the reference supplied as GenBank rather than GFF3.

    Everything asserted above must hold identically -- which is the point: a user
    dropping a .gbk and a breseq folder carrying data/reference.gff3 should get
    the same annotation.
    """

    reference_path = SYNTHETIC_GBK


class UnannotatedImportTestCase(TestCase):
    """Import still succeeds when annotation cannot run; it just does not annotate.

    A .gd can legitimately arrive before a usable reference does, so a missing or
    mismatched reference means "not annotated yet", not "this import is invalid".
    """

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, is_staff=True,
                            date_joined=datetime.now())
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]

    def _establish(self, path):
        gff3_text, sequences = reference.normalize_reference(path)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences)

    def _import(self):
        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name="syn exp", person="tester")

    def _categories(self):
        return set(Mutation.objects.values_list("mutation_category", flat=True))

    def test_a_reference_row_whose_stored_file_is_gone(self):
        # Possible for experiments predating the store, or after a store wipe.
        self._establish(SYNTHETIC_GFF3)
        os.unlink(store.experiment_reference_path(
            self.experiment.id, store.REFERENCE_GFF3))

        self._import()

        self.assertIsNone(
            reference_store.annotation_reference_path(self.experiment.id))
        self.assertEqual(36, Mutation.objects.count())
        self.assertLessEqual(self._categories(), {None, ""})

    def test_mutations_that_match_no_contig_in_the_reference(self):
        """Refused at import, not imported unannotated.

        This used to import the lot and leave every record without annotation,
        because the annotator silently skips a seq_id it cannot resolve. The
        result was an experiment holding mutations on a contig its own reference
        had never heard of, visible only as annotation that never appeared.
        """
        other = os.path.join(self.store, "other.gff3")
        with open(other, "w") as handle:
            handle.write(
                "##gff-version 3\n##sequence-region\tOTHER\t1\t12\n"
                "OTHER\tx\tCDS\t1\t9\t.\t+\t0\tID=g1;Name=g1\n"
                "##FASTA\n>OTHER\nACGTACGTACGT\n")
        self._establish(other)

        self._import()

        self.assertEqual(0, Mutation.objects.count())

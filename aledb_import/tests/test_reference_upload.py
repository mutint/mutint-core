import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import reference as reference_io
from aledb_import import reference_store
from aledb_import.tests import breseq_fixture
from aledb_seq.models import ExperimentReference

SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A)]


def write_genbank(path, sequences=SEQUENCES, with_features=True):
    """Write a real GenBank file with Biopython, so the parser is exercised for real."""
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import SeqFeature, FeatureLocation
    from Bio.SeqRecord import SeqRecord

    records = []
    for seq_id, sequence in sequences:
        record = SeqRecord(Seq(sequence), id=seq_id, name=seq_id, description="test")
        record.annotations["molecule_type"] = "DNA"
        if with_features:
            # Biopython locations are 0-based half-open, so this is GFF3 50..150.
            location = FeatureLocation(49, 150, strand=1)
            record.features.append(
                SeqFeature(location, type="gene", qualifiers={"gene": ["thrA"]}))
            record.features.append(
                SeqFeature(location, type="CDS",
                           qualifiers={"product": ["aspartokinase"]}))
        records.append(record)
    SeqIO.write(records, path, "genbank")
    return path


class NormalizationTestCase(TestCase):
    """Normalization exists so the same genome hashes the same however it arrived."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def test_detects_each_format(self):
        gbk = write_genbank(os.path.join(self.tmp, "ref.gbk"))
        gff = self._write("ref.gff3", breseq_fixture.gff3_text(SEQUENCES))
        fasta = self._write("ref.fasta", breseq_fixture.fasta_text(SEQUENCES))

        self.assertEqual(reference_io.detect_format(gbk), reference_io.FORMAT_GENBANK)
        self.assertEqual(reference_io.detect_format(gff), reference_io.FORMAT_GFF3)
        self.assertEqual(reference_io.detect_format(fasta), reference_io.FORMAT_FASTA)

    def test_detects_format_by_content_when_the_name_is_unhelpful(self):
        path = self._write("reference.txt", breseq_fixture.fasta_text(SEQUENCES))
        self.assertEqual(reference_io.detect_format(path), reference_io.FORMAT_FASTA)

    def test_unrecognisable_file_is_rejected(self):
        path = self._write("ref.txt", "this is not a reference genome\n")
        with self.assertRaises(reference_io.ReferenceFormatError):
            reference_io.detect_format(path)

    def test_genbank_and_gff3_of_the_same_genome_normalize_identically(self):
        """The property the whole design rests on."""
        gbk = write_genbank(os.path.join(self.tmp, "ref.gbk"))
        gff = self._write("ref.gff3", breseq_fixture.gff3_text(SEQUENCES))

        from_gbk = reference_io.normalize_reference(gbk)
        from_gff = reference_io.normalize_reference(gff)

        self.assertEqual(from_gbk[0], from_gff[0], "normalized GFF3 differs")
        self.assertEqual(from_gbk[1], from_gff[1], "normalized sequences differ")
        self.assertEqual(reference_store.digest(from_gbk[0]),
                         reference_store.digest(from_gff[0]))

    def test_normalized_gff3_is_breseq_dialect(self):
        """The canonical form keeps what annotation needs, in breseq's spelling.

        It used to be reduced to bare `gene` rows carrying a name and a product.
        That was enough to hash and to draw, but not to annotate: breseq's gene
        list excludes type `gene` outright, so a reduced file annotates every
        mutation as intergenic. See aledb_import/annotate/gff3.py.
        """
        gff3_text, _sequences = reference_io.normalize_reference(
            write_genbank(os.path.join(self.tmp, "ref.gbk")))

        # Only the annotation half; everything after ##FASTA is sequence.
        annotation = gff3_text.split("##FASTA", 1)[0]
        feature_rows = [line for line in annotation.splitlines()
                        if line and not line.startswith("#")]
        self.assertEqual(len(feature_rows), 1)

        # Coding type preserved, so a SNP here can be translated.
        self.assertIn("\tCDS\t", feature_rows[0])
        self.assertNotIn("\tgene\t", gff3_text)
        # breseq's attribute spelling: Note for the product, plus the table.
        self.assertIn("Note=aspartokinase", feature_rows[0])
        self.assertIn("Name=thrA", feature_rows[0])
        self.assertIn("transl_table=", feature_rows[0])

    def test_the_normalized_form_can_be_annotated_against(self):
        """The point of the exercise: reload the stored form and it still works."""
        from aledb_import.annotate.loader import load_reference

        path = os.path.join(self.tmp, "normalized.gff3")
        gff3_text, _sequences = reference_io.normalize_reference(
            write_genbank(os.path.join(self.tmp, "ref.gbk")))
        with open(path, "w") as handle:
            handle.write(gff3_text)

        references = load_reference(path)
        genes = references["test_ref"].gene_locations
        self.assertEqual(1, len(genes))
        self.assertEqual("thrA", genes[0].feature.name)
        self.assertEqual("CDS", genes[0].feature.type)

    def test_normalization_is_idempotent(self):
        """Re-normalizing the canonical form reproduces it exactly.

        If it did not, re-importing a stored reference would look like an
        annotation change and churn gff3_sha256.
        """
        path = os.path.join(self.tmp, "normalized.gff3")
        once, _sequences = reference_io.normalize_reference(
            write_genbank(os.path.join(self.tmp, "ref.gbk")))
        with open(path, "w") as handle:
            handle.write(once)

        twice, _sequences = reference_io.normalize_reference(path)
        self.assertEqual(once, twice)

    def test_fasta_only_reference_has_sequence_and_no_genes(self):
        path = self._write("ref.fasta", breseq_fixture.fasta_text(SEQUENCES))
        gff3_text, sequences = reference_io.normalize_reference(path)

        self.assertEqual(sequences, SEQUENCES)
        self.assertNotIn("\tgene\t", gff3_text)
        self.assertIn("##FASTA", gff3_text)

    def test_gff3_without_sequence_is_rejected(self):
        path = self._write(
            "ref.gff3", "##gff-version 3\ntest_ref\tx\tgene\t1\t9\t.\t+\t.\tID=g1\n")
        with self.assertRaises(reference_io.ReferenceFormatError):
            reference_io.normalize_reference(path)

    def test_normalization_is_case_and_wrapping_insensitive(self):
        wrapped = breseq_fixture.fasta_text(SEQUENCES, line_length=10).lower()
        a = self._write("a.fasta", breseq_fixture.fasta_text(SEQUENCES))
        b = self._write("b.fasta", wrapped)

        self.assertEqual(reference_io.normalize_reference(a),
                         reference_io.normalize_reference(b))


class ReferenceStoreTestCase(TestCase):
    """What `/import/reference/` used to assert over HTTP, asserted on the store directly.

    The page is gone (see `aledb_import/views.py`); `establish_or_check` is where its
    behaviour actually lived, and the Add page's `reference` / `replace_annotation` types now
    reach it. The routing half of those types is covered in `test_import_registry`.
    """

    def setUp(self):
        # _prepare_experiment -> try_creating_project -> find_user, which prompts on stdin
        # for a name that matches no User. The user has to exist first.
        User.objects.create(username="tester", email="t@e.com", is_active=True)

        self.tmp = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _experiment(self, name="ref exp"):
        from aledb_import.gd_import import _prepare_experiment
        return _prepare_experiment("ref project", name, "tester", False)["experiment"]

    def _write(self, filename, content):
        path = os.path.join(self.tmp, filename)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def _establish(self, experiment, filename, content, **kwargs):
        path = self._write(filename, content)
        gff3_text, sequences = reference_io.normalize_reference(path, filename)
        return reference_store.establish_or_check(
            experiment, gff3_text, sequences, **kwargs)

    def _genbank_bytes(self):
        path = write_genbank(os.path.join(self.tmp, "ref.gbk"))
        with open(path, "rb") as handle:
            return handle.read()

    def _stored_gff3(self, reference):
        with open(store.experiment_reference_path(
                reference.ale_experiment_id, store.REFERENCE_GFF3), encoding="utf-8") as handle:
            return handle.read()

    def test_genbank_sets_the_reference(self):
        reference, created = self._establish(
            self._experiment(), "REL606.gbk", self._genbank_bytes())

        self.assertTrue(created)
        self.assertEqual(reference.total_length, len(breseq_fixture.SEQUENCE_A))
        self.assertEqual([s["id"] for s in reference.seq_ids], ["test_ref"])
        self.assertIn("Name=thrA", self._stored_gff3(reference))

        for artifact in (store.REFERENCE_GFF3, store.REFERENCE_FASTA, store.REFERENCE_FAI):
            self.assertTrue(os.path.isfile(store.experiment_reference_path(
                reference.ale_experiment_id, artifact)), artifact)

    def test_fasta_sets_a_reference_with_no_genes(self):
        reference, _ = self._establish(
            self._experiment(), "ref.fasta",
            breseq_fixture.fasta_text(SEQUENCES).encode("utf-8"))

        self.assertNotIn("\tgene\t", self._stored_gff3(reference))

    def test_gff3_matches_the_genbank_of_the_same_genome(self):
        """Whichever format it arrived as, the same genome must hash the same."""
        gbk_ref, _ = self._establish(
            self._experiment("from gbk"), "REL606.gbk", self._genbank_bytes())
        gff_ref, _ = self._establish(
            self._experiment("from gff"), "REL606.gff3",
            breseq_fixture.gff3_text(SEQUENCES).encode("utf-8"))

        self.assertEqual(gbk_ref.gff3_sha256, gff_ref.gff3_sha256)
        self.assertEqual(gbk_ref.fasta_sha256, gff_ref.fasta_sha256)

    def test_unreadable_reference_is_refused(self):
        with self.assertRaises(reference_io.ReferenceFormatError) as caught:
            self._establish(self._experiment(), "notes.txt", b"just some notes\n")
        self.assertIn("not recognisable", str(caught.exception))

    def test_a_different_sequence_is_refused_unless_replace_is_set(self):
        experiment = self._experiment()
        self._establish(experiment, "REL606.gbk", self._genbank_bytes())
        other = breseq_fixture.fasta_text(
            [("test_ref", breseq_fixture.SEQUENCE_B)]).encode("utf-8")

        with self.assertRaises(reference_store.ReferenceMismatch):
            self._establish(experiment, "other.fasta", other)

        # `replace` is the shell-only escape hatch: no UI passes it, deliberately.
        reference, created = self._establish(
            experiment, "other.fasta", other, replace=True)
        self.assertFalse(created)
        self.assertEqual(ExperimentReference.objects.count(), 1)
        self.assertEqual(reference.total_length, len(breseq_fixture.SEQUENCE_B))

    def test_reuploading_the_same_reference_is_accepted(self):
        experiment = self._experiment()
        self._establish(experiment, "REL606.gbk", self._genbank_bytes())
        self._establish(experiment, "REL606.gbk", self._genbank_bytes())
        self.assertEqual(ExperimentReference.objects.count(), 1)

    def test_same_sequence_with_new_annotation_refreshes_it(self):
        """Sequence is the invariant, so this is the same reference -- but an explicit
        upload of richer annotation is a request to use that annotation."""
        experiment = self._experiment()
        before, _ = self._establish(
            experiment, "plain.fasta",
            breseq_fixture.fasta_text(SEQUENCES).encode("utf-8"),
            update_annotation=True)
        before_gff3_sha = before.gff3_sha256
        self.assertEqual(before.total_length, len(breseq_fixture.SEQUENCE_A))

        after, created = self._establish(
            experiment, "REL606.gbk", self._genbank_bytes(), update_annotation=True)

        self.assertFalse(created)
        self.assertEqual(after.pk, before.pk)
        self.assertEqual(after.fasta_sha256, before.fasta_sha256)   # same genome
        self.assertNotEqual(after.gff3_sha256, before_gff3_sha)     # richer annotation
        # The stored GFF3 on disk was refreshed too, not just the hash.
        self.assertIn("Name=thrA", self._stored_gff3(after))

    def test_without_update_annotation_the_stored_one_is_left_alone(self):
        """A breseq folder import must not let drop order redefine the annotation."""
        experiment = self._experiment()
        before, _ = self._establish(
            experiment, "plain.fasta",
            breseq_fixture.fasta_text(SEQUENCES).encode("utf-8"))
        before_gff3_sha = before.gff3_sha256

        after, _ = self._establish(experiment, "REL606.gbk", self._genbank_bytes())
        self.assertEqual(after.gff3_sha256, before_gff3_sha)

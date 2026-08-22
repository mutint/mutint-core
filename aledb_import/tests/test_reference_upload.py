import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
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

    def test_normalized_gff3_keeps_only_genes(self):
        gff3_text, _sequences = reference_io.normalize_reference(
            write_genbank(os.path.join(self.tmp, "ref.gbk")))

        # Only the annotation half; everything after ##FASTA is sequence.
        annotation = gff3_text.split("##FASTA", 1)[0]
        feature_rows = [line for line in annotation.splitlines()
                        if line and not line.startswith("#")]
        self.assertEqual(len(feature_rows), 1)
        self.assertIn("\tgene\t", feature_rows[0])
        self.assertNotIn("\tCDS\t", gff3_text)
        # The CDS's product was carried onto the gene it shares coordinates with.
        self.assertIn("product=aspartokinase", feature_rows[0])
        self.assertIn("Name=thrA", feature_rows[0])

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


class ReferenceUploadEndpointTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.tmp = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def _post(self, filename, content, experiment="ref exp", **extra):
        data = {"project": "ref project", "experiment": experiment, "person": "tester",
                "reference": SimpleUploadedFile(filename, content)}
        data.update(extra)
        return self.client.post("/import/reference/", data)

    def _genbank_bytes(self):
        path = write_genbank(os.path.join(self.tmp, "ref.gbk"))
        with open(path, "rb") as handle:
            return handle.read()

    def test_page_renders(self):
        response = self.client.get("/import/reference/")
        self.assertEqual(response.status_code, 200)

    def test_genbank_upload_sets_the_reference(self):
        response = self._post("REL606.gbk", self._genbank_bytes())

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["genes"], 1)
        self.assertEqual(body["total_length"], len(breseq_fixture.SEQUENCE_A))
        self.assertEqual([s["id"] for s in body["sequences"]], ["test_ref"])

        reference = ExperimentReference.objects.get()
        for artifact in (store.REFERENCE_GFF3, store.REFERENCE_FASTA, store.REFERENCE_FAI):
            self.assertTrue(os.path.isfile(store.experiment_reference_path(
                reference.ale_experiment_id, artifact)), artifact)

    def test_fasta_upload_sets_a_reference_with_no_genes(self):
        response = self._post(
            "ref.fasta", breseq_fixture.fasta_text(SEQUENCES).encode("utf-8"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["genes"], 0)

    def test_gff3_upload_matches_the_genbank_of_the_same_genome(self):
        """Uploading either format must leave the experiment in the same state."""
        self._post("REL606.gbk", self._genbank_bytes(), experiment="from gbk")
        self._post("REL606.gff3",
                   breseq_fixture.gff3_text(SEQUENCES).encode("utf-8"),
                   experiment="from gff")

        gbk_ref, gff_ref = ExperimentReference.objects.order_by("ale_experiment_id")
        self.assertEqual(gbk_ref.gff3_sha256, gff_ref.gff3_sha256)
        self.assertEqual(gbk_ref.fasta_sha256, gff_ref.fasta_sha256)

    def test_unreadable_reference_is_a_400(self):
        response = self._post("notes.txt", b"just some notes\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not recognisable", response.json()["error"])

    def test_missing_fields_are_a_400(self):
        response = self.client.post("/import/reference/", {"project": "p"})
        self.assertEqual(response.status_code, 400)

    def test_a_different_reference_is_refused_unless_replace_is_set(self):
        self._post("REL606.gbk", self._genbank_bytes())

        other = [("test_ref", breseq_fixture.SEQUENCE_B)]
        response = self._post(
            "other.fasta", breseq_fixture.fasta_text(other).encode("utf-8"))
        self.assertEqual(response.status_code, 409)
        self.assertIn("replace", response.json()["error"])

        response = self._post(
            "other.fasta", breseq_fixture.fasta_text(other).encode("utf-8"), replace="on")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertEqual(ExperimentReference.objects.count(), 1)
        self.assertEqual(ExperimentReference.objects.get().total_length,
                         len(breseq_fixture.SEQUENCE_B))

    def test_reuploading_the_same_reference_is_accepted(self):
        self._post("REL606.gbk", self._genbank_bytes())
        response = self._post("REL606.gbk", self._genbank_bytes())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExperimentReference.objects.count(), 1)

    def test_same_sequence_with_new_annotation_refreshes_it(self):
        """Sequence is the invariant, so this is the same reference -- but an explicit
        upload of richer annotation is a request to use that annotation."""
        self._post("plain.fasta", breseq_fixture.fasta_text(SEQUENCES).encode("utf-8"))
        before = ExperimentReference.objects.get()
        self.assertEqual(before.total_length, len(breseq_fixture.SEQUENCE_A))

        response = self._post("REL606.gbk", self._genbank_bytes())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["genes"], 1)

        after = ExperimentReference.objects.get()
        self.assertEqual(after.pk, before.pk)
        self.assertEqual(after.fasta_sha256, before.fasta_sha256)   # same genome
        self.assertNotEqual(after.gff3_sha256, before.gff3_sha256)  # richer annotation

        # The stored GFF3 on disk was refreshed too, not just the hash.
        with open(store.experiment_reference_path(
                after.ale_experiment_id, store.REFERENCE_GFF3), encoding="utf-8") as handle:
            self.assertIn("Name=thrA", handle.read())


class BareGdRequiresAReferenceTestCase(TestCase):
    """The two-step route exists so bare .gd import has a reference to sit against."""

    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.tmp = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        fixture_dir = os.path.join(
            os.path.dirname(__file__), "..", "gdparse", "test_gdparse")
        with open(os.path.join(fixture_dir, "3-30000-1-1.gd"), "rb") as handle:
            self.gd_bytes = handle.read()

    def _import_gd(self, experiment):
        return self.client.post("/import/", {
            "project": "step project", "experiment": experiment, "person": "tester",
            "gd_files": SimpleUploadedFile("3-30000-1-1.gd", self.gd_bytes)})

    def test_import_without_a_reference_is_refused(self):
        response = self._import_gd("no reference yet")
        # A missing precondition is the caller's error, not a server fault.
        self.assertEqual(response.status_code, 400)
        self.assertIn("reference genome", response.json()["error"])

    def test_import_succeeds_after_step_one(self):
        step_one = self.client.post("/import/reference/", {
            "project": "step project", "experiment": "two step",
            "person": "tester",
            "reference": SimpleUploadedFile(
                "ref.fasta", breseq_fixture.fasta_text(SEQUENCES).encode("utf-8"))})
        self.assertEqual(step_one.status_code, 200, step_one.content)

        response = self._import_gd("two step")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreater(response.json()["total_mutations"], 0)

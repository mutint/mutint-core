import os
import shutil
import tempfile

from django.test import SimpleTestCase

from mutint_import import reference
from mutint_import.tests import breseq_fixture


class FastaIndexTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, text, name="reference.fasta", newline="\n"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
        return path

    def _read_fai(self, path):
        with open(path, encoding="utf-8") as handle:
            return [line.rstrip("\n").split("\t") for line in handle if line.strip()]

    def test_fai_matches_hand_computed_offsets(self):
        # ">ref\n" is 5 bytes, so the first base starts at byte 5; 70-base lines are
        # 71 bytes each with the newline.
        path = self._write(">ref\n" + "A" * 70 + "\n" + "C" * 30 + "\n")
        rows = self._read_fai(reference.write_fai(path))

        self.assertEqual(len(rows), 1)
        name, length, offset, line_bases, line_width = rows[0]
        self.assertEqual(name, "ref")
        self.assertEqual(int(length), 100)
        self.assertEqual(int(offset), 5)
        self.assertEqual(int(line_bases), 70)
        self.assertEqual(int(line_width), 71)

    def test_fai_offsets_account_for_crlf(self):
        """Byte offsets, not character counts -- a CRLF file must still index correctly."""
        path = self._write(">ref\r\n" + "A" * 10 + "\r\n", newline="")
        rows = self._read_fai(reference.write_fai(path))
        _name, length, offset, line_bases, line_width = rows[0]
        self.assertEqual(int(length), 10)
        self.assertEqual(int(offset), 6)      # ">ref\r\n"
        self.assertEqual(int(line_bases), 10)
        self.assertEqual(int(line_width), 12)  # 10 bases + CRLF

    def test_fai_handles_multiple_records(self):
        sequences = [("a", breseq_fixture.SEQUENCE_A), ("b", breseq_fixture.SEQUENCE_B)]
        path = self._write(breseq_fixture.fasta_text(sequences))
        rows = self._read_fai(reference.write_fai(path))

        self.assertEqual([r[0] for r in rows], ["a", "b"])
        self.assertEqual(int(rows[0][1]), len(breseq_fixture.SEQUENCE_A))
        self.assertEqual(int(rows[1][1]), len(breseq_fixture.SEQUENCE_B))
        # The second record's offset must point past the first record's bytes.
        self.assertGreater(int(rows[1][2]), int(rows[0][2]) + int(rows[0][1]))

    def test_ragged_lines_are_rejected(self):
        """A short line is legal only as a record's last line; .fai cannot express otherwise."""
        path = self._write(">ref\nAAAA\nCC\nGGGG\n")
        with self.assertRaises(ValueError):
            reference.write_fai(path)


class Gff3TestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write_gff3(self, text):
        path = os.path.join(self.tmp, "reference.gff3")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def test_reads_features_and_inline_fasta(self):
        sequences = [("test_ref", breseq_fixture.SEQUENCE_A)]
        parsed = reference.read_gff3(self._write_gff3(breseq_fixture.gff3_text(sequences)))

        self.assertEqual(len(parsed["features"]), 1)
        feature = parsed["features"][0]
        self.assertEqual(feature["seq_id"], "test_ref")
        self.assertEqual(feature["type"], "gene")
        self.assertEqual(feature["start"], 50)
        self.assertEqual(feature["end"], 150)
        self.assertEqual(feature["attributes"]["Name"], "thrA")

        self.assertEqual(parsed["sequences"], sequences)

    def test_gff3_without_fasta_section_yields_no_sequences(self):
        parsed = reference.read_gff3(self._write_gff3(
            "##gff-version 3\ntest_ref\tbreseq\tgene\t1\t9\t.\t+\t.\tID=g1\n"))
        self.assertEqual(parsed["sequences"], [])
        self.assertEqual(len(parsed["features"]), 1)

    def test_parse_fasta_takes_first_header_token_as_id(self):
        import io
        records = list(reference.parse_fasta(io.StringIO(">ref desc here\nACGT\n")))
        self.assertEqual(records, [("ref", "ACGT")])

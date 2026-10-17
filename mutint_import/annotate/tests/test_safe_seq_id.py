"""`safe_seq_id` is a port of breseq's `safe_seq_id_name` and must agree with it."""

import os
import subprocess
import tempfile
import unittest

from django.test import SimpleTestCase

from mutint_common.tools import tool_environment, tool_path
from mutint_import.annotate.model import LoadedReferenceSequences, safe_seq_id

# Every shape the rule has an opinion about, each with a safe form no other name here
# shares: breseq keeps the *last* of two sequences whose names collapse onto one id, with a
# warning and nothing else, where MutInt refuses. Names with whitespace are not here because
# a FASTA header ends its name at the first space, in breseq and in `parse_fasta` alike.
CONFORMANCE_NAMES = (
    "NC_000913", "REL606.6", "k141_12", "p-ant1+x",
    "P-Ant1_(_ColE1-carbR-GF", "p(1)", "a|b/c,d", "e___f", "__g__", "(chr)", "chr.2+",
    "h\u2212i", "caf\u00e9", "x:y", "j;k", "q=1&2", "[l]", "m'n", 'o"p', "r*s",
)


class SafeSeqIdTestCase(SimpleTestCase):

    def test_a_safe_name_is_unchanged(self):
        for name in ("NC_000913", "REL606.6", "p-ant1+x", "k141_12"):
            self.assertEqual(safe_seq_id(name), name)

    def test_a_unicode_minus_sign_is_not_kept(self):
        # breseq's set is ASCII only now; it carried U+2212 for years, as bytes.
        self.assertEqual(safe_seq_id("a\u2212b"), "a_b")

    def test_a_disallowed_character_becomes_an_underscore_and_runs_collapse(self):
        # What breseq printed for the file this was written for.
        self.assertEqual(safe_seq_id("P-Ant1_(_ColE1-carbR-GF"), "P-Ant1_ColE1-carbR-GF")
        self.assertEqual(safe_seq_id("a b"), "a_b")
        self.assertEqual(safe_seq_id("a|b/c,d"), "a_b_c_d")
        self.assertEqual(safe_seq_id("a___b"), "a_b")

    def test_leading_and_trailing_underscores_are_dropped(self):
        self.assertEqual(safe_seq_id("(chr)"), "chr")
        self.assertEqual(safe_seq_id("__x__"), "x")

    def test_a_name_with_no_valid_character_is_refused(self):
        with self.assertRaises(ValueError):
            safe_seq_id("()")
        with self.assertRaises(ValueError):
            safe_seq_id("")


class BreseqNameTestCase(SimpleTestCase):

    def test_a_rename_is_recorded_and_an_unchanged_name_is_not(self):
        references = LoadedReferenceSequences()
        self.assertEqual(references.breseq_name("chr"), "chr")
        self.assertEqual(references.breseq_name("p(1)"), "p_1")
        self.assertEqual(references.renamed, [("p(1)", "p_1")])

    def test_two_names_collapsing_onto_one_are_refused(self):
        from mutint_import.annotate.model import AnnotatedSequence

        references = LoadedReferenceSequences()
        references.add(AnnotatedSequence("p_1", "ACGT"))
        with self.assertRaises(ValueError) as caught:
            references.breseq_name("p(1)")
        self.assertIn("p_1", str(caught.exception))


class BreseqConformanceTestCase(SimpleTestCase):
    """The port must agree with breseq itself, which is the only authority on the rule.

    `breseq CONVERT-REFERENCE --list-seq-ids` prints each sequence's name in the file beside
    the seq id breseq uses. breseq is provisioned only where a component asks for it
    (mutint-breseq's `tools.txt`), so standalone core skips this.
    """

    def test_the_port_agrees_with_breseq(self):
        breseq = tool_path("breseq")
        if breseq is None:
            raise unittest.SkipTest("breseq is not installed; the port is unchecked here")

        with tempfile.TemporaryDirectory() as tmp:
            fasta = os.path.join(tmp, "names.fasta")
            with open(fasta, "w", encoding="utf-8") as handle:
                for name in CONFORMANCE_NAMES:
                    handle.write(">%s\nACGTACGTACGT\n" % name)
            listing = os.path.join(tmp, "seq_ids.tsv")
            run = subprocess.run(
                [breseq, "CONVERT-REFERENCE", "--list-seq-ids", "-o", listing, fasta],
                env=tool_environment(), capture_output=True, text=True, timeout=120)
            if "Unknown command argument option: list-seq-ids" in run.stdout + run.stderr:
                raise unittest.SkipTest("this breseq predates --list-seq-ids")
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            with open(listing, encoding="utf-8") as handle:
                rows = [line.rstrip("\n").split("\t") for line in handle if line.strip()]

        self.assertEqual([original for original, _safe in rows], list(CONFORMANCE_NAMES))
        for original, safe in rows:
            self.assertEqual(safe_seq_id(original), safe, original)

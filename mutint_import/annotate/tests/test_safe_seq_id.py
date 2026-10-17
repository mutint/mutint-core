"""`safe_seq_id` is a port of breseq's `safe_seq_id_name` and must agree with it."""

from django.test import SimpleTestCase

from mutint_import.annotate.model import LoadedReferenceSequences, safe_seq_id


class SafeSeqIdTestCase(SimpleTestCase):

    def test_a_safe_name_is_unchanged(self):
        for name in ("NC_000913", "REL606.6", "p-ant1+x", "k141_12", "a\u2212b"):
            self.assertEqual(safe_seq_id(name), name)

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

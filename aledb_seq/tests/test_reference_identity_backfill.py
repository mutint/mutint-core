"""0009's backfill rule.

The rule inlines its own FASTA reader and digests rather than importing
`aledb_import.reference`, because a migration must not re-interpret history when the app's
idea of a hash changes. The first test is what keeps that duplication honest.
"""

import os
import shutil
import tempfile

from django.test import TestCase

from aledb_import import reference as reference_io
from aledb_seq.migrations import _reference_identity as rule


class BackfillRuleTestCase(TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _fasta(self, body):
        path = os.path.join(self.dir, "reference.fasta")
        with open(path, "w") as handle:
            handle.write(body)
        return path

    def test_the_migration_agrees_with_the_app(self):
        """Two copies of a hash that disagree would silently split one genome in two. This
        is a red test rather than a silent divergence."""
        sequences = [("a", "ACGTACGT"), ("b", "GGTTAACC")]

        self.assertEqual(reference_io.sequence_set_digest(sequences),
                         rule.sequence_set_digest(sequences))
        for _name, sequence in sequences:
            self.assertEqual(reference_io.sequence_digest(sequence),
                             rule.sequence_digest(sequence))

    def test_identity_is_read_from_a_fasta(self):
        path = self._fasta(">chr desc here\nACGT\nACGT\n>plasmid\nGGTT\n")

        digest, entries, total = rule.identity_from_fasta(path)

        self.assertEqual(reference_io.sequence_set_digest(
            [("chr", "ACGTACGT"), ("plasmid", "GGTT")]), digest)
        # The id is the first whitespace token; the description is not part of it.
        self.assertEqual(["chr", "plasmid"], [e["id"] for e in entries])
        self.assertEqual([8, 4], [e["length"] for e in entries])
        self.assertTrue(all(e["sha256"] for e in entries))
        self.assertEqual(12, total)

    def test_a_missing_file_is_not_an_error(self):
        """A deployment holds a reference row whose files never reached the store. A
        migration that raised on it would block every later migration on that instance."""
        self.assertIsNone(rule.identity_from_fasta(os.path.join(self.dir, "absent.fasta")))

    def test_an_empty_fasta_is_not_an_error(self):
        self.assertIsNone(rule.identity_from_fasta(self._fasta("")))

    def test_wrapping_and_case_do_not_change_the_answer(self):
        wide = rule.identity_from_fasta(self._fasta(">x\n" + "ACGT" * 20 + "\n"))
        narrow = rule.identity_from_fasta(self._fasta(">x\n" + "\n".join(["acgt"] * 20) + "\n"))

        self.assertEqual(wide[0], narrow[0])

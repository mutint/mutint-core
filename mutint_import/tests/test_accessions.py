"""Turning what somebody typed into the accessions box into a list of accessions.

Pure, so these are the cheapest tests in the feature and the ones that pin the rules most
likely to be got wrong by accident: what separates two accessions, and which database each one
belongs to.
"""

from django.test import SimpleTestCase

from mutint_import import accessions


class ParseTestCase(SimpleTestCase):
    def test_nothing_typed_is_no_accessions(self):
        # Not an error: "no accessions" is a perfectly good thing for the box to say, and it
        # is the caller that knows whether files were dropped as well.
        self.assertEqual(accessions.parse(""), [])
        self.assertEqual(accessions.parse("   \n "), [])
        self.assertEqual(accessions.parse(None), [])

    def test_every_separator_the_page_promises(self):
        for text in ("NC_000913.3,U00096.3",
                     "NC_000913.3 U00096.3",
                     "NC_000913.3;U00096.3",
                     "NC_000913.3\nU00096.3",
                     "NC_000913.3\tU00096.3",
                     "NC_000913.3, ;\r\n U00096.3",
                     "  NC_000913.3 ,U00096.3  "):
            self.assertEqual(accessions.parse(text), ["NC_000913.3", "U00096.3"], text)

    def test_the_order_typed_is_kept(self):
        # It decides which of two accessions naming the same bases keeps the link -- see
        # `upload_session._record_downloads` -- so it is not merely cosmetic.
        self.assertEqual(accessions.parse("U00096.3 NC_000913.3"),
                         ["U00096.3", "NC_000913.3"])

    def test_a_repeat_is_dropped_however_it_is_spelled(self):
        # One accession is one download; case is NCBI's business, not two records.
        self.assertEqual(accessions.parse("NC_000913.3, nc_000913.3, NC_000913.3"),
                         ["NC_000913.3"])

    def test_a_token_that_could_not_be_an_accession_is_refused_by_name(self):
        with self.assertRaises(accessions.AccessionError) as caught:
            accessions.parse("NC_000913.3, ../etc/passwd")
        self.assertIn("../etc/passwd", str(caught.exception))

    def test_characters_are_checked_and_shape_is_not(self):
        """The doctrine this feature had to stay inside.

        A pattern strict enough to decide what an accession *looks like* would refuse formats
        it was not written for, and that failure is invisible -- the token would simply never
        become eligible. So anything made of accession characters is passed on for NCBI to
        answer for, including a name that is obviously not an accession at all.
        """
        self.assertEqual(accessions.parse("REL606"), ["REL606"])
        self.assertEqual(accessions.parse("NZ_CP009273.1"), ["NZ_CP009273.1"])
        self.assertEqual(accessions.parse("AP012306.1"), ["AP012306.1"])

    def test_more_than_a_pageful_is_refused(self):
        text = " ".join("NC_%06d.1" % n for n in range(accessions.MAX_ACCESSIONS + 1))
        with self.assertRaises(accessions.AccessionError) as caught:
            accessions.parse(text)
        self.assertIn(str(accessions.MAX_ACCESSIONS), str(caught.exception))

    def test_a_pasted_sentence_is_refused_rather_than_sent_to_ncbi(self):
        with self.assertRaises(accessions.AccessionError):
            accessions.parse("x" * (accessions.MAX_TOKEN_LENGTH + 1))


class KindTestCase(SimpleTestCase):
    def test_the_assembly_namespace_is_the_whole_rule(self):
        for token in ("GCF_000005845.2", "GCA_000005845.2", "gcf_000005845.2"):
            self.assertEqual(accessions.kind(token), accessions.ASSEMBLY, token)

    def test_everything_else_is_a_nucleotide_accession(self):
        for token in ("NC_000913.3", "U00096.3", "NZ_CP009273.1", "REL606", "GC_000005845"):
            self.assertEqual(accessions.kind(token), accessions.NUCLEOTIDE, token)

    def test_each_kind_can_be_named_in_a_sentence(self):
        # The refusals say which database was asked, which is what makes a wrongly-routed
        # token recoverable rather than a flat "not found".
        self.assertIn("nucleotide", accessions.database_name("NC_000913.3"))
        self.assertIn("genome", accessions.database_name("GCF_000005845.2"))

"""Confirming a reference contig against NCBI.

Everything here patches `requests.get`. **No test may reach the network**: NCBI would make the
suite slow, flaky and dependent on a third party's uptime, and the interesting cases -- a
sibling strain of exactly equal length, a truncated download -- are ones we cannot ask NCBI to
produce on demand anyway.
"""

from unittest import mock

from django.db.utils import IntegrityError
from django.test import TestCase, override_settings

from mutint_import.reference import render_fasta, sequence_digest
from mutint_sample import ncbi
from mutint_sample.models import DatabaseSequenceLink

BASES = "ACGTAAGGTTCCATGCATGCATGCAAATTTCCCGGGTACGTACGTACGTAGCTAGCTAGCTA" * 4
DIGEST = sequence_digest(BASES)
LENGTH = len(BASES)


def _summary(accession="NC_000913.3", slen=LENGTH, uid="556503834"):
    return {"result": {"uids": [uid],
                       uid: {"accessionversion": accession, "slen": slen}}}


class _Response:
    """The parts of a requests response these two calls actually touch."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_lines(self):
        for line in self._text.splitlines():
            yield line

    def close(self):
        pass


class DigestAgreementTestCase(TestCase):
    """The streaming digest must equal the stored one, and this is the pin that says so.

    `sequence_digest_stream` is a second implementation of `sequence_digest`, written so a
    genome is never held in memory whole. If the two ever diverge every verdict silently
    becomes meaningless -- everything would report MISMATCH -- so their agreement is asserted
    rather than assumed.
    """

    def test_the_streaming_digest_matches_the_stored_one(self):
        text = render_fasta([("NC_000913", BASES)])
        streamed, counted = ncbi.sequence_digest_stream(text.splitlines())
        self.assertEqual(streamed, sequence_digest(BASES))
        self.assertEqual(counted, LENGTH)

    def test_case_and_line_wrapping_do_not_change_it(self):
        """The importer uppercases before hashing; a lowercase FASTA must hash the same."""
        wrapped = ">x\n" + "\n".join(BASES[i:i + 13].lower() for i in range(0, LENGTH, 13))
        streamed, _ = ncbi.sequence_digest_stream(wrapped.splitlines())
        self.assertEqual(streamed, sequence_digest(BASES))

    def test_headers_and_blank_lines_are_not_bases(self):
        text = ">one desc here\n\n%s\n\n" % BASES
        _digest, counted = ncbi.sequence_digest_stream(text.splitlines())
        self.assertEqual(counted, LENGTH)


class VerifyTestCase(TestCase):
    def _verify(self, responses, accession="NC_000913"):
        with mock.patch("mutint_sample.ncbi.requests.get", side_effect=responses) as get:
            result = ncbi.verify(DIGEST, LENGTH, accession)
        return result, get

    def test_an_exact_match_verifies(self):
        (status, accession, detail), get = self._verify([
            _Response(payload=_summary()),
            _Response(text=render_fasta([("NC_000913", BASES)])),
        ])
        self.assertEqual(status, DatabaseSequenceLink.VERIFIED)
        self.assertIn("matches this contig exactly", detail)
        self.assertEqual(get.call_count, 2)

    def test_the_versioned_accession_is_what_gets_stored(self):
        """Somebody types the unversioned name; what we keep is the version that matched.

        The point of the exercise is to end holding an identifier that names exactly the
        sequence which was compared, so the viewer draws that one and not whatever is current.
        """
        (_status, accession, _detail), _get = self._verify([
            _Response(payload=_summary(accession="NC_000913.3")),
            _Response(text=render_fasta([("NC_000913", BASES)])),
        ], accession="NC_000913")
        self.assertEqual(accession, "NC_000913.3")

    def test_a_length_difference_is_rejected_without_downloading_anything(self):
        """The cheap stage has to actually be cheap, or it is not worth having.

        A wrong accession is the common case and a genome is a large thing to fetch to find
        out; the summary settles it for one small request.
        """
        (status, _accession, detail), get = self._verify([
            _Response(payload=_summary(slen=LENGTH + 1000)),
        ])
        self.assertEqual(status, DatabaseSequenceLink.MISMATCH)
        self.assertEqual(get.call_count, 1)
        self.assertIn("different sequences", detail)

    def test_the_same_length_and_different_bases_is_a_mismatch(self):
        """The case the length check cannot catch: a sibling strain, or another assembly."""
        other = "T" + BASES[1:]
        self.assertEqual(len(other), LENGTH)
        (status, _accession, detail), get = self._verify([
            _Response(payload=_summary()),
            _Response(text=render_fasta([("NC_000913", other)])),
        ])
        self.assertEqual(status, DatabaseSequenceLink.MISMATCH)
        self.assertEqual(get.call_count, 2)
        self.assertIn("not the same sequence", detail)

    def test_no_such_record_is_not_a_mismatch(self):
        (status, _accession, detail), _get = self._verify([
            _Response(payload={"result": {"uids": []}}),
        ])
        self.assertEqual(status, DatabaseSequenceLink.NOT_FOUND)
        self.assertIn("no nucleotide record", detail)

    def test_a_truncated_download_is_an_error_not_a_mismatch(self):
        """We cannot say the sequences differ when we did not receive one of them."""
        (status, _accession, detail), _get = self._verify([
            _Response(payload=_summary()),
            _Response(text=render_fasta([("NC_000913", BASES[:100])])),
        ])
        self.assertEqual(status, DatabaseSequenceLink.ERROR)
        self.assertIn("but sent", detail)

    def test_a_network_failure_is_an_error(self):
        import requests
        with mock.patch("mutint_sample.ncbi.requests.get",
                        side_effect=requests.ConnectionError("no route to host")):
            status, _accession, detail = ncbi.verify(DIGEST, LENGTH, "NC_000913")
        self.assertEqual(status, DatabaseSequenceLink.ERROR)
        self.assertIn("Could not reach NCBI", detail)

    def test_a_non_json_answer_is_an_error(self):
        (status, _accession, detail), _get = self._verify([_Response(payload=None)])
        self.assertEqual(status, DatabaseSequenceLink.ERROR)
        self.assertIn("not JSON", detail)

    def test_an_http_error_is_an_error(self):
        (status, _accession, _detail), _get = self._verify([_Response(status_code=500)])
        self.assertEqual(status, DatabaseSequenceLink.ERROR)

    def test_an_empty_accession_asks_nobody_anything(self):
        """Nothing is inferred, so a contig nobody has named makes no request at all."""
        with mock.patch("mutint_sample.ncbi.requests.get") as get:
            status, accession, detail = ncbi.verify(DIGEST, LENGTH, "")
        self.assertEqual(status, DatabaseSequenceLink.UNCHECKED)
        self.assertEqual(accession, "")
        self.assertEqual(detail, "")
        get.assert_not_called()

    @override_settings(MUTINT_NCBI_MAX_BASES=100)
    def test_an_implausibly_large_record_is_refused_before_downloading(self):
        (status, _accession, detail), get = self._verify([
            _Response(payload=_summary()),
        ])
        self.assertEqual(status, DatabaseSequenceLink.ERROR)
        self.assertEqual(get.call_count, 1)
        self.assertIn("ceiling", detail)


class CheckAndStoreTestCase(TestCase):
    def test_a_failed_check_is_stored_too(self):
        """A MISMATCH somebody has already paid for should not be re-fetched by the next
        reader, and an ERROR is what `--list` shows to somebody who was not watching."""
        with mock.patch("mutint_sample.ncbi.requests.get",
                        side_effect=[_Response(payload=_summary(slen=1))]):
            record = ncbi.check_and_store(DIGEST, LENGTH, "NC_000913")
        self.assertEqual(record.status, DatabaseSequenceLink.MISMATCH)
        self.assertTrue(record.detail)
        self.assertIsNotNone(record.checked_at)
        self.assertEqual(DatabaseSequenceLink.objects.count(), 1)

    def test_rechecking_updates_the_same_row(self):
        with mock.patch("mutint_sample.ncbi.requests.get",
                        side_effect=[_Response(payload=_summary(slen=1))]):
            ncbi.check_and_store(DIGEST, LENGTH, "NC_000913")
        with mock.patch("mutint_sample.ncbi.requests.get", side_effect=[
                _Response(payload=_summary()),
                _Response(text=render_fasta([("NC_000913", BASES)]))]):
            record = ncbi.check_and_store(DIGEST, LENGTH, "NC_000913")

        self.assertEqual(DatabaseSequenceLink.objects.count(), 1)
        self.assertEqual(record.status, DatabaseSequenceLink.VERIFIED)
        self.assertTrue(record.is_verified)


class DatabaseScopeTestCase(TestCase):
    """What the `database` column bought, which is only a schema property today.

    There is one database and one module that can speak to one, so nothing in the product
    exercises a second value. These two assert the *table* no longer presumes -- which is the
    whole deliverable, and which no other test can see.
    """

    OTHER = "ENA"

    def test_one_digest_can_be_linked_in_two_databases(self):
        """`sha256` was `unique=True`, so this was a schema error rather than a design
        choice. The key is `(database, sha256)` now: the same bases may be a record in
        several databases, and each is its own verdict."""
        DatabaseSequenceLink.objects.create(
            sha256=DIGEST, length=LENGTH, accession="NC_000913.3",
            status=DatabaseSequenceLink.VERIFIED)
        DatabaseSequenceLink.objects.create(
            database=self.OTHER, sha256=DIGEST, length=LENGTH, accession="U00096.3",
            status=DatabaseSequenceLink.VERIFIED)

        self.assertEqual(2, DatabaseSequenceLink.objects.filter(sha256=DIGEST).count())

    def test_the_same_digest_twice_in_one_database_is_still_refused(self):
        """The other half. Loosening the key must not have loosened it to nothing -- a
        second verdict for one sequence in one database is still a contradiction."""
        DatabaseSequenceLink.objects.create(sha256=DIGEST, length=LENGTH)

        with self.assertRaises(IntegrityError):
            DatabaseSequenceLink.objects.create(sha256=DIGEST, length=LENGTH)

    def test_a_lookup_does_not_see_another_databases_verdict(self):
        """The reason every lookup grew a `database` argument. Unscoped, a row somebody
        recorded in another database would answer here -- so a contig would read as verified
        against NCBI on the strength of an ENA accession, and the page would draw NCBI's
        viewer for it."""
        DatabaseSequenceLink.objects.create(
            database=self.OTHER, sha256=DIGEST, length=LENGTH, accession="U00096.3",
            status=DatabaseSequenceLink.VERIFIED)

        self.assertIsNone(ncbi.record_for(DIGEST))
        self.assertEqual({}, ncbi.records_for([DIGEST]))
        self.assertIsNotNone(ncbi.record_for(DIGEST, database=self.OTHER))

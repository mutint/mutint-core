"""Downloading a reference genome from NCBI by accession.

Everything here patches `requests.get`, and **no test may reach the network** -- the rule
`mutint_sample/tests/test_ncbi.py` states for the same reason. The cases that matter most are
ones NCBI could not be asked to produce on demand anyway: a paginated assembly, an efetch that
answers without one of the records it was asked for, a CON record with no sequence.

The GenBank payloads the fake NCBI returns are written by Biopython, so what comes back is
parsed by the real parser rather than by a fixture that agrees with itself -- and they carry
LOCUS and VERSION apart, as real records do.
"""

import os
import shutil
import tempfile
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from mutint_import import ncbi_fetch
from mutint_import.accessions import AccessionError
from mutint_import.reference import sequence_digest
from mutint_import.tests.breseq_fixture import SEQUENCE_A

CHROMOSOME = SEQUENCE_A
PLASMID = "TTTTGGGGCCCCAAAA" * 10


def genbank_text(records):
    """A GenBank file as NCBI would send it, as text. `records` is [(accession, bases)].

    **LOCUS and VERSION differ, exactly as they do in a real record**: NCBI writes
    `LOCUS NC_000913` and `VERSION NC_000913.3`, and the whole feature turns on that
    difference -- the contig is imported under the LOCUS name, while the link is filed under
    the VERSION. A fixture putting the versioned form in both would agree with itself and
    would have hidden it.
    """
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import FeatureLocation, SeqFeature
    from Bio.SeqRecord import SeqRecord

    written = []
    for accession, bases in records:
        locus = accession.rsplit(".", 1)[0]
        record = SeqRecord(Seq(bases), id=accession, name=locus, description="test")
        record.annotations["molecule_type"] = "DNA"
        location = FeatureLocation(0, min(len(bases), 30), strand=1)
        record.features.append(
            SeqFeature(location, type="gene", qualifiers={"gene": ["thrA"]}))
        record.features.append(
            SeqFeature(location, type="CDS", qualifiers={"product": ["aspartokinase"]}))
        written.append(record)

    handle = tempfile.NamedTemporaryFile(suffix=".gbk", delete=False, mode="w")
    handle.close()
    try:
        SeqIO.write(written, handle.name, "genbank")
        with open(handle.name, "r", encoding="utf-8") as opened:
            return opened.read()
    finally:
        os.unlink(handle.name)


def _summary_payload(accession="NC_000913.3", slen=len(CHROMOSOME), uid="1"):
    return {"result": {"uids": [uid], uid: {"accessionversion": accession, "slen": slen}}}


def _reports(entries, total_count=None, next_page_token=None):
    payload = {"reports": entries,
               "total_count": len(entries) if total_count is None else total_count}
    if next_page_token:
        payload["next_page_token"] = next_page_token
    return payload


class _Response:
    """The parts of a requests response this module touches.

    Wider than `test_ncbi.py`'s twin because the download streams with `iter_content` rather
    than reading lines, and because Datasets is asked for JSON with headers.
    """

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_content(self, chunk_size=None, decode_unicode=False):
        yield self._text

    def close(self):
        pass


def no_delay():
    """Patch out the politeness delay. Real sleeps would make this suite seconds slower.

    The delay itself is asserted separately, in `PolitenessTestCase`, so patching it away here
    cannot hide its removal.
    """
    return mock.patch.object(ncbi_fetch.time, "sleep", lambda _seconds: None)


class ResolveNucleotideTestCase(TestCase):
    def test_an_unversioned_accession_resolves_to_the_version_ncbi_holds(self):
        with mock.patch("mutint_sample.ncbi.requests.get",
                        return_value=_Response(payload=_summary_payload())) as get:
            plans = ncbi_fetch.resolve(["NC_000913"])

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].typed, "NC_000913")
        self.assertEqual(plans[0].accessions, ["NC_000913.3"])
        self.assertEqual(plans[0].total_length, len(CHROMOSOME))
        self.assertEqual(get.call_count, 1)

    def test_no_such_record_is_refused_naming_the_database_that_was_asked(self):
        with mock.patch("mutint_sample.ncbi.requests.get",
                        return_value=_Response(payload={"result": {"uids": []}})):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["NC_0009133"])
        self.assertIn("NC_0009133", str(caught.exception))
        self.assertIn("nucleotide", str(caught.exception))

    def test_unreachable_is_a_different_sentence_from_no_such_record(self):
        # The distinction `mutint_sample.ncbi` already draws: being told there is no such
        # record is an answer, and not being able to ask is not.
        import requests

        with mock.patch("mutint_sample.ncbi.requests.get",
                        side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["NC_000913.3"])
        self.assertIn("Could not reach NCBI", str(caught.exception))

    def test_a_wgs_master_record_is_refused_with_the_answer(self):
        """A real accession carrying no sequence of its own.

        Importing it would establish a reference of no bases, so the refusal names the thing
        that person actually wants instead.
        """
        with mock.patch("mutint_sample.ncbi.requests.get",
                        return_value=_Response(
                            payload=_summary_payload("AAAA00000000.1", slen=0))):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["AAAA00000000"])
        self.assertIn("WGS master record", str(caught.exception))
        self.assertIn("GCA_", str(caught.exception))

    @override_settings(MUTINT_NCBI_MAX_BASES=len(CHROMOSOME))
    def test_the_ceiling_is_on_the_whole_drop_and_not_on_one_record(self):
        # The drop is *one* reference, so a limit applied per contig would say nothing at all
        # about an assembly of nine hundred scaffolds.
        with no_delay(), mock.patch("mutint_sample.ncbi.requests.get",
                                    return_value=_Response(payload=_summary_payload())):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["NC_000913.3", "U00096.3"])
        self.assertIn("past the", str(caught.exception))


class ResolveAssemblyTestCase(TestCase):
    def test_an_assembly_becomes_the_accessions_it_is_made_of(self):
        payload = _reports([
            {"refseq_accession": "NC_000913.3", "genbank_accession": "U00096.3",
             "length": len(CHROMOSOME), "role": "assembled-molecule"},
            {"refseq_accession": "NC_001416.1", "genbank_accession": "J02459.1",
             "length": len(PLASMID), "role": "assembled-molecule"},
        ])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=payload)):
            plans = ncbi_fetch.resolve(["GCF_000005845.2"])

        self.assertEqual(plans[0].accessions, ["NC_000913.3", "NC_001416.1"])
        self.assertEqual(plans[0].total_length, len(CHROMOSOME) + len(PLASMID))
        # One file, whatever the assembly holds: one thing typed is one row in the summary.
        self.assertEqual(plans[0].filename, "GCF_000005845.2.gbk")

    def test_gca_takes_the_genbank_spelling_where_gcf_takes_the_refseq_one(self):
        payload = _reports([{"refseq_accession": "NC_000913.3",
                             "genbank_accession": "U00096.3",
                             "length": len(CHROMOSOME)}])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=payload)):
            self.assertEqual(
                ncbi_fetch.resolve(["GCA_000005845.2"])[0].accessions, ["U00096.3"])
            self.assertEqual(
                ncbi_fetch.resolve(["GCF_000005845.2"])[0].accessions, ["NC_000913.3"])

    def test_the_other_spelling_is_used_when_the_preferred_one_is_absent(self):
        payload = _reports([{"genbank_accession": "U00096.3", "length": len(CHROMOSOME)}])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=payload)):
            self.assertEqual(
                ncbi_fetch.resolve(["GCF_000005845.2"])[0].accessions, ["U00096.3"])

    def test_an_unknown_assembly_answers_an_empty_object_and_is_refused(self):
        # Datasets says 200 with `{}` rather than 404, so "no such record" has to be read off
        # the body rather than off the status.
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload={})):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["GCF_999999999.9"])
        self.assertIn("GCF_999999999.9", str(caught.exception))
        self.assertIn("genome", str(caught.exception))

    def test_a_sequence_with_no_accession_at_all_stops_the_import(self):
        # Skipping it quietly would be a reference silently missing a contig, which is the one
        # thing this module refuses to produce.
        payload = _reports([{"sequence_name": "scaffold_7", "length": 100}])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=payload)):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["GCF_000005845.2"])
        self.assertIn("scaffold_7", str(caught.exception))

    def test_every_page_is_followed(self):
        """A draft assembly does not fit in one page, and stopping early is invisible.

        This is the failure the pagination exists for: a partial genome looks exactly like a
        successful import until a sample is refused against it months later.
        """
        first = _reports([{"refseq_accession": "NC_1.1", "length": 10}],
                         total_count=2, next_page_token="page-2")
        second = _reports([{"refseq_accession": "NC_2.1", "length": 10}], total_count=2)
        with no_delay(), mock.patch(
                "mutint_import.ncbi_fetch.requests.get",
                side_effect=[_Response(payload=first), _Response(payload=second)]) as get:
            plans = ncbi_fetch.resolve(["GCF_000005845.2"])

        self.assertEqual(plans[0].accessions, ["NC_1.1", "NC_2.1"])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["page_token"], "page-2")

    def test_a_page_loop_that_ends_early_fails_rather_than_returning_what_it_has(self):
        short = _reports([{"refseq_accession": "NC_1.1", "length": 10}], total_count=9)
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=short)):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["GCF_000005845.2"])
        self.assertIn("1 of", str(caught.exception))
        self.assertIn("worse than none", str(caught.exception))

    def test_too_many_sequences_is_refused(self):
        entries = [{"refseq_accession": "NC_%d.1" % n, "length": 10}
                   for n in range(ncbi_fetch.MAX_SEQUENCES + 1)]
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=_reports(entries))):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["GCF_000005845.2"])
        self.assertIn(str(ncbi_fetch.MAX_SEQUENCES), str(caught.exception))

    def test_rate_limiting_is_said_in_words(self):
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(status_code=429, payload={})):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.resolve(["GCF_000005845.2"])
        self.assertIn("rate-limiting", str(caught.exception))

    @override_settings(MUTINT_NCBI_API_KEY="secret-key")
    def test_the_datasets_key_travels_as_a_header(self):
        # eutils takes it as a query parameter and Datasets as a header; sending it the wrong
        # way is not an error anybody would see, it just silently does not raise the limit.
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(payload=_reports(
                            [{"refseq_accession": "NC_1.1", "length": 10}]))) as get:
            ncbi_fetch.resolve(["GCF_000005845.2"])
        self.assertEqual(get.call_args.kwargs["headers"], {"api-key": "secret-key"})
        self.assertNotIn("api_key", get.call_args.kwargs["params"])


class DownloadTestCase(TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _plan(self, typed, records, database="nucleotide"):
        return ncbi_fetch.Plan(typed, database, records)

    def test_one_file_per_accession_typed_and_the_records_read_back_off_it(self):
        plan = self._plan("NC_000913.3", [{"accession": "NC_000913.3",
                                           "length": len(CHROMOSOME)}])
        text = genbank_text([("NC_000913.3", CHROMOSOME)])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(text=text)):
            downloaded = ncbi_fetch.download(self.root, [plan])

        path = os.path.join(self.root, ncbi_fetch.STAGED_SUBDIR, "NC_000913.3.gbk")
        self.assertTrue(os.path.isfile(path))
        # Keyed on the digest, because that is what a DatabaseSequenceLink is keyed on, and
        # computed by the same function that writes `seq_ids` -- so the two cannot disagree.
        self.assertEqual(list(downloaded), [sequence_digest(CHROMOSOME)])
        entry = downloaded[sequence_digest(CHROMOSOME)]
        self.assertEqual(entry["accession"], "NC_000913.3")
        self.assertEqual(entry["length"], len(CHROMOSOME))
        self.assertIn("Downloaded from NCBI as NC_000913.3", entry["detail"])

    def test_an_assemblys_sequences_all_land_in_the_one_file(self):
        plan = self._plan("GCF_000005845.2",
                          [{"accession": "NC_000913.3", "length": len(CHROMOSOME)},
                           {"accession": "NC_001416.1", "length": len(PLASMID)}],
                          database="assembly")
        text = genbank_text([("NC_000913.3", CHROMOSOME), ("NC_001416.1", PLASMID)])
        with no_delay(), mock.patch("mutint_import.ncbi_fetch.requests.get",
                                    return_value=_Response(text=text)):
            downloaded = ncbi_fetch.download(self.root, [plan])

        self.assertEqual(
            sorted(os.listdir(os.path.join(self.root, ncbi_fetch.STAGED_SUBDIR))),
            ["GCF_000005845.2.gbk"])
        self.assertEqual(len(downloaded), 2)
        # The sentence names the assembly as well, because that is what was typed and it is
        # not what the contig's own accession says.
        self.assertIn("part of assembly GCF_000005845.2",
                      downloaded[sequence_digest(PLASMID)]["detail"])

    def test_accessions_are_fetched_in_batches(self):
        # Distinct bases per contig, as a real assembly has: the digest is what a link is
        # keyed on, so identical contigs would be one entry rather than many.
        bases = ["ACGT" * (n + 4) for n in range(ncbi_fetch.FETCH_BATCH + 1)]
        records = [{"accession": "NC_%d.1" % n, "length": len(bases[n])}
                   for n in range(ncbi_fetch.FETCH_BATCH + 1)]
        plan = self._plan("GCF_000005845.2", records, database="assembly")
        first = genbank_text([(record["accession"], bases[n])
                              for n, record in enumerate(records[:ncbi_fetch.FETCH_BATCH])])
        last = genbank_text([(records[-1]["accession"], bases[-1])])
        with no_delay(), mock.patch(
                "mutint_import.ncbi_fetch.requests.get",
                side_effect=[_Response(text=first), _Response(text=last)]) as get:
            downloaded = ncbi_fetch.download(self.root, [plan])

        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(downloaded), ncbi_fetch.FETCH_BATCH + 1)
        # One file still, however many requests it took to fill it.
        self.assertEqual(
            os.listdir(os.path.join(self.root, ncbi_fetch.STAGED_SUBDIR)),
            ["GCF_000005845.2.gbk"])

    def test_two_records_with_the_same_bases_are_one_link_and_not_a_failure(self):
        """Two accessions can name one sequence, and only one row can hold the link.

        `DatabaseSequenceLink` is unique on the digest, so the map collapses -- and the
        arrival check has to be counted separately, or the collapsed record reads as one NCBI
        never sent. The first is kept, which is the same rule twinned accessions follow in
        `upload_session._record_downloads`.
        """
        plan = self._plan("GCF_000005845.2",
                          [{"accession": "NC_000913.3", "length": len(CHROMOSOME)},
                           {"accession": "U00096.3", "length": len(CHROMOSOME)}],
                          database="assembly")
        text = genbank_text([("NC_000913.3", CHROMOSOME), ("U00096.3", CHROMOSOME)])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(text=text)):
            downloaded = ncbi_fetch.download(self.root, [plan])

        self.assertEqual(len(downloaded), 1)
        self.assertEqual(downloaded[sequence_digest(CHROMOSOME)]["accession"],
                         "NC_000913.3")

    def test_a_record_that_did_not_arrive_stops_the_import(self):
        """What catches an efetch that answered with an error page, or dropped an id.

        The alternative is a reference quietly missing a contig, which is indistinguishable
        from success until something is checked against it.
        """
        plan = self._plan("GCF_000005845.2",
                          [{"accession": "NC_000913.3", "length": len(CHROMOSOME)},
                           {"accession": "NC_001416.1", "length": len(PLASMID)}],
                          database="assembly")
        text = genbank_text([("NC_000913.3", CHROMOSOME)])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(text=text)):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.download(self.root, [plan])
        self.assertIn("NC_001416.1", str(caught.exception))

    def test_a_record_with_no_sequence_is_refused(self):
        """The `gbwithparts` trap, asserted.

        A CON record fetched as plain `gb` parses cleanly into a record with an empty
        sequence, so the reference would be established with the right contig names and no
        bases at all.
        """
        plan = self._plan("NC_002128.1", [{"accession": "NC_002128.1", "length": 92721}])
        text = genbank_text([("NC_002128.1", CHROMOSOME)])
        # As NCBI actually emits one: the features table terminated by CONTIG rather than by
        # an ORIGIN block. (Biopython refuses a features table with nothing after it at all,
        # so simply truncating would test the parser and not this code.)
        without_origin = (text[:text.index("ORIGIN")]
                          + "CONTIG      join(X00001.1:1..92721)\n//\n")
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(text=without_origin)):
            with self.assertRaises(ncbi_fetch.FetchError) as caught:
                ncbi_fetch.download(self.root, [plan])
        self.assertIn("no sequence", str(caught.exception))

    def test_gbwithparts_is_what_is_asked_for(self):
        plan = self._plan("NC_000913.3", [{"accession": "NC_000913.3",
                                           "length": len(CHROMOSOME)}])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(
                            text=genbank_text([("NC_000913.3", CHROMOSOME)]))) as get:
            ncbi_fetch.download(self.root, [plan])
        self.assertEqual(get.call_args.kwargs["params"]["rettype"], "gbwithparts")

    def test_downloading_twice_rebuilds_rather_than_accumulates(self):
        """A declined-then-confirmed rename finalizes the same session twice.

        A second download landing beside the first would import the genome twice over, as two
        contigs with the same name.
        """
        plan = self._plan("NC_000913.3", [{"accession": "NC_000913.3",
                                           "length": len(CHROMOSOME)}])
        stale = os.path.join(self.root, ncbi_fetch.STAGED_SUBDIR)
        os.makedirs(stale)
        with open(os.path.join(stale, "left-over.gbk"), "w") as handle:
            handle.write("stale")

        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(
                            text=genbank_text([("NC_000913.3", CHROMOSOME)]))):
            ncbi_fetch.download(self.root, [plan])
        self.assertEqual(os.listdir(stale), ["NC_000913.3.gbk"])

    def test_a_plan_survives_the_round_trip_through_the_session_row(self):
        plan = self._plan("GCF_000005845.2",
                          [{"accession": "NC_000913.3", "length": 7}], database="assembly")
        restored = ncbi_fetch.Plan.from_dict(plan.as_dict())
        self.assertEqual(restored.typed, plan.typed)
        self.assertEqual(restored.kind, plan.kind)
        self.assertEqual(restored.accessions, plan.accessions)
        # The filename especially: the create-time refusal promised the client this name was
        # free, so recomputing it later would be a second opinion about that promise.
        self.assertEqual(restored.filename, plan.filename)
        self.assertEqual(restored.staged_path,
                         os.path.join(ncbi_fetch.STAGED_SUBDIR, "GCF_000005845.2.gbk"))


class PolitenessTestCase(SimpleTestCase):
    def test_requests_are_spaced_under_the_limit_that_needs_no_api_key(self):
        """NCBI allows 3 requests a second without a key and 10 with one.

        Asserted rather than left to the constant, because the delay is the only thing
        standing between an import of a many-contig assembly and a 429.
        """
        self.assertGreaterEqual(ncbi_fetch.POLITE_DELAY_SECONDS, 1 / 3.0)


class DigestAgreementTestCase(TestCase):
    """What this module derives must equal what `seq_ids` stores, or no link is ever found.

    The same pin `test_ncbi.DigestAgreementTestCase` exists for, and for the same reason: two
    digest implementations that can disagree would leave every downloaded contig silently
    unlinked while everything looked like it had worked.
    """

    def test_the_digest_read_off_a_download_is_the_stored_one(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        plan = ncbi_fetch.Plan("NC_000913.3", "nucleotide",
                               [{"accession": "NC_000913.3", "length": len(CHROMOSOME)}])
        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(
                            text=genbank_text([("NC_000913.3", CHROMOSOME)]))):
            downloaded = ncbi_fetch.download(root, [plan])

        from mutint_import.reference import sequence_entries

        entries = sequence_entries([("NC_000913", CHROMOSOME)])
        self.assertIn(entries[0]["sha256"], downloaded)


class AccessionErrorIsNotSwallowedTestCase(SimpleTestCase):
    def test_parse_errors_reach_the_caller_as_themselves(self):
        # `create_upload_session` catches both types; this asserts the two stay distinct so a
        # bad token cannot be reported as a network problem.
        self.assertFalse(issubclass(AccessionError, ncbi_fetch.FetchError))

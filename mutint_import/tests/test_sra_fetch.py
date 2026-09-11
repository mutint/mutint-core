"""Fetching reads from the SRA through ENA.

Everything here patches `requests.get`, and **no test may reach the network** -- the rule
`test_ncbi_fetch.py` states, for the same reasons. The cases that matter most are the ones
ENA could not be asked to produce on demand: a run with no FASTQ, a file that arrives with the
wrong checksum, a download cancelled halfway.
"""

import hashlib
import os
import shutil
import tempfile
from unittest import mock

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from mutint_import import sra, sra_fetch
from mutint_import.accessions import AccessionError
from mutint_jobs.processes import Cancelled

READS = b"@r1\nACGT\n+\nIIII\n" * 64


def _row(run="SRR2584863", sample="SAMN04096083", alias="REL768A", files=None):
    """One report row. `files` is `[(name, bytes)]`; the MD5 is computed from the bytes."""
    if files is None:
        files = [("%s_1.fastq.gz" % run, READS), ("%s_2.fastq.gz" % run, READS[::-1])]
    base = "ftp.sra.ebi.ac.uk/vol1/fastq/%s/%s" % (run, run)
    return {
        "run_accession": run, "sample_accession": sample, "secondary_sample_accession": "",
        "experiment_accession": "SRX1", "study_accession": "PRJNA1",
        "sample_alias": alias, "sample_title": "t", "library_layout": "PAIRED",
        "fastq_ftp": ";".join("%s/%s" % (base.rsplit("/", 1)[0], name) for name, _ in files),
        "fastq_md5": ";".join(hashlib.md5(data).hexdigest() for _, data in files),
        "fastq_bytes": ";".join(str(len(data)) for _, data in files),
    }


class _Response:
    """The parts of a requests response this module touches."""

    def __init__(self, status_code=200, payload=None, chunks=(), raise_after=None):
        self.status_code = status_code
        self._payload = payload
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_content(self, chunk_size=None):
        for index, chunk in enumerate(self._chunks):
            if self._raise_after is not None and index == self._raise_after:
                raise requests.ConnectionError("connection reset by ftp.sra.ebi.ac.uk")
            yield chunk

    def close(self):
        self.closed = True


def fake_ena(rows_by_token, bytes_by_name=None, chunk=16):
    """A `requests.get` that answers the file report from `rows_by_token` and serves files.

    `bytes_by_name` maps a filename to the bytes served for it, in `chunk`-sized pieces --
    several, so a cancellation between chunks has somewhere to land.
    """
    bytes_by_name = bytes_by_name or {}

    def get(url, params=None, timeout=None, stream=False):
        if url == sra_fetch.FILEREPORT_URL:
            return _Response(payload=rows_by_token.get(params["accession"], []))
        name = url.rsplit("/", 1)[1]
        if name not in bytes_by_name:
            return _Response(status_code=404)
        data = bytes_by_name[name]
        return _Response(chunks=[data[i:i + chunk] for i in range(0, len(data), chunk)])
    return get


def no_delay():
    return mock.patch.object(sra_fetch.time, "sleep", lambda _seconds: None)


def no_poll_gap():
    """Ask `is_cancelled` on every chunk rather than every two seconds."""
    return mock.patch.object(sra_fetch, "CANCEL_POLL_SECONDS", 0)


class ResolveTestCase(TestCase):
    def test_one_request_per_token_with_the_fields_ena_needs(self):
        rows = {"SRR2584863": [_row()], "SAMN2": [_row(run="SRR7", sample="SAMN2")]}
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena(rows)) as get:
            plans = sra_fetch.resolve(["SRR2584863", "SAMN2"])

        self.assertEqual(get.call_count, 2)
        params = get.call_args_list[0].kwargs["params"]
        self.assertEqual(params["accession"], "SRR2584863")
        self.assertEqual(params["result"], "read_run")
        for field in ("fastq_ftp", "fastq_md5", "fastq_bytes", "sample_accession",
                      "sample_alias"):
            self.assertIn(field, params["fields"])
        self.assertEqual([plan.typed for plan in plans], ["SRR2584863", "SAMN2"])
        self.assertEqual(plans[0].kind, sra.RUN)
        self.assertEqual(plans[1].kind, sra.SAMPLE)
        self.assertEqual(plans[1].run_accessions, ["SRR7"])

    def test_an_unknown_accession_is_refused_by_name(self):
        with mock.patch("mutint_import.sra_fetch.requests.get", side_effect=fake_ena({})):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SRR99999999"])
        self.assertIn("SRR99999999", str(caught.exception))
        self.assertIn("run", str(caught.exception))

    def test_a_token_of_no_sra_shape_is_refused_before_ena_is_asked(self):
        with mock.patch("mutint_import.sra_fetch.requests.get") as get:
            with self.assertRaises(AccessionError):
                sra_fetch.resolve(["NC_000913.3"])
        get.assert_not_called()

    def test_a_run_with_no_fastq_is_refused_naming_the_run(self):
        row = _row(run="SRR5", sample="SAMN5")
        row["fastq_ftp"] = row["fastq_md5"] = row["fastq_bytes"] = ""
        with mock.patch("mutint_import.sra_fetch.requests.get",
                        side_effect=fake_ena({"SAMN5": [_row(run="SRR4", sample="SAMN5"),
                                                        row]})):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SAMN5"])
        self.assertIn("SRR5", str(caught.exception))
        self.assertIn("SAMN5", str(caught.exception))
        self.assertIn("no FASTQ", str(caught.exception))

    def test_a_run_named_twice_is_refused_naming_both_tokens(self):
        rows = {"SRP1": [_row(run="SRR1"), _row(run="SRR2")], "SRR2": [_row(run="SRR2")]}
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena(rows)):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SRP1", "SRR2"])
        self.assertIn("SRR2 is named twice", str(caught.exception))
        self.assertIn("SRP1", str(caught.exception))

    @override_settings(MUTINT_SRA_MAX_RUNS=2)
    def test_more_runs_than_the_cap_are_refused_naming_the_setting(self):
        rows = {"SRP1": [_row(run="SRR%d" % n) for n in range(3)]}
        with mock.patch("mutint_import.sra_fetch.requests.get", side_effect=fake_ena(rows)):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SRP1"])
        self.assertIn("3 runs", str(caught.exception))
        self.assertIn("MUTINT_SRA_MAX_RUNS", str(caught.exception))

    @override_settings(MUTINT_SRA_MAX_BYTES=100)
    def test_more_bytes_than_the_cap_are_refused_naming_the_setting(self):
        with mock.patch("mutint_import.sra_fetch.requests.get",
                        side_effect=fake_ena({"SRR2584863": [_row()]})):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SRR2584863"])
        self.assertIn("MUTINT_SRA_MAX_BYTES", str(caught.exception))

    def test_unreachable_is_a_sentence_naming_the_token(self):
        with mock.patch("mutint_import.sra_fetch.requests.get",
                        side_effect=requests.ConnectionError("dns failed")):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SRR2584863"])
        self.assertIn("Could not reach ENA", str(caught.exception))
        self.assertIn("SRR2584863", str(caught.exception))

    def test_a_non_200_is_a_sentence_with_the_status(self):
        with mock.patch("mutint_import.sra_fetch.requests.get",
                        return_value=_Response(status_code=503)):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.resolve(["SRR2584863"])
        self.assertIn("HTTP 503", str(caught.exception))

    def test_requests_are_spaced_politely(self):
        rows = {"SRR1": [_row(run="SRR1")], "SRR2": [_row(run="SRR2")],
                "SRR3": [_row(run="SRR3")]}
        with mock.patch.object(sra_fetch.time, "sleep") as sleep, \
                mock.patch("mutint_import.sra_fetch.requests.get", side_effect=fake_ena(rows)):
            sra_fetch.resolve(["SRR1", "SRR2", "SRR3"])
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(sleep.call_args.args, (sra_fetch.POLITE_DELAY_SECONDS,))


class DownloadTestCase(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.plan = sra.Plan("SRR2584863", sra.RUN, [sra.run_from_row(_row())])
        self.files = {"SRR2584863_1.fastq.gz": READS, "SRR2584863_2.fastq.gz": READS[::-1]}

    def _listing(self, directory=None):
        return sorted(os.listdir(directory or self.root))

    def test_a_download_is_written_verified_and_mapped_to_its_run(self):
        target = os.path.join(self.root, "reads")   # does not exist yet
        reported = []
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena({}, self.files)):
            downloaded = sra_fetch.download(target, [self.plan.as_dict()],
                                            report=reported.append)

        self.assertEqual(self._listing(target),
                         ["SRR2584863_1.fastq.gz", "SRR2584863_2.fastq.gz"])
        with open(os.path.join(target, "SRR2584863_2.fastq.gz"), "rb") as handle:
            self.assertEqual(handle.read(), READS[::-1])
        self.assertEqual(downloaded, {"SRR2584863_1.fastq.gz": "SRR2584863",
                                      "SRR2584863_2.fastq.gz": "SRR2584863"})
        self.assertEqual(len(reported), 2)
        self.assertIn("SRR2584863_1.fastq.gz (1 of 2", reported[0])

    def test_a_wrong_checksum_removes_the_file_and_says_so(self):
        served = dict(self.files, **{"SRR2584863_2.fastq.gz": b"x" * len(READS)})
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena({}, served)):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.download(self.root, [self.plan])
        self.assertIn("checksum", str(caught.exception))
        self.assertIn("SRR2584863_2.fastq.gz", str(caught.exception))
        # The first file, which was whole, stays: a failed run keeps everything.
        self.assertEqual(self._listing(), ["SRR2584863_1.fastq.gz"])

    def test_a_short_download_removes_the_file(self):
        served = dict(self.files, **{"SRR2584863_1.fastq.gz": READS[:100]})
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena({}, served)):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.download(self.root, [self.plan])
        self.assertIn("cut short", str(caught.exception))
        self.assertEqual(self._listing(), [])

    def test_a_connection_dropped_midway_leaves_no_part_file(self):
        def get(url, params=None, timeout=None, stream=False):
            return _Response(chunks=[READS[:16], READS[16:]], raise_after=1)

        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get", side_effect=get):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.download(self.root, [self.plan])
        self.assertIn("stopped partway", str(caught.exception))
        self.assertEqual(self._listing(), [])

    def test_a_404_names_the_file(self):
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena({}, {})):
            with self.assertRaises(sra_fetch.FetchError) as caught:
                sra_fetch.download(self.root, [self.plan])
        self.assertIn("HTTP 404", str(caught.exception))
        self.assertIn("SRR2584863_1.fastq.gz", str(caught.exception))

    def test_cancellation_between_chunks_raises_cancelled_and_leaves_nothing(self):
        answers = iter([False, False, True] + [True] * 100)
        with no_delay(), no_poll_gap(), mock.patch(
                "mutint_import.sra_fetch.requests.get", side_effect=fake_ena({}, self.files)):
            with self.assertRaises(Cancelled):
                sra_fetch.download(self.root, [self.plan],
                                   is_cancelled=lambda: next(answers))
        self.assertEqual(self._listing(), [])

    def test_cancellation_is_asked_on_the_clock_not_per_chunk(self):
        asked = []
        with no_delay(), mock.patch("mutint_import.sra_fetch.requests.get",
                                    side_effect=fake_ena({}, self.files)):
            sra_fetch.download(self.root, [self.plan],
                               is_cancelled=lambda: asked.append(1) and False)
        # Two files, each asked once at the end; a two-second gap never elapses in a test.
        self.assertEqual(len(asked), 2)
        self.assertEqual(self._listing(), ["SRR2584863_1.fastq.gz", "SRR2584863_2.fastq.gz"])

    def test_two_runs_writing_one_name_are_refused_before_anything_is_fetched(self):
        second = sra.run_from_row(_row(run="SRR9"))
        second.files[0]["name"] = "SRR2584863_1.fastq.gz"
        with mock.patch("mutint_import.sra_fetch.requests.get") as get:
            with self.assertRaises(AccessionError):
                sra_fetch.download(self.root, [self.plan, sra.Plan("SRR9", sra.RUN, [second])])
        get.assert_not_called()

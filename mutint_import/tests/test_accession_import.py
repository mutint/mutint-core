"""Importing a reference genome from an NCBI accession, through the real endpoints.

Driven the way `test_upload_session.py` drives an upload -- POST to `/import/uploads/`, then
`/finalize` -- because the whole point of the design is that accessions travel through the
machinery files already travel through. `requests.get` is patched throughout; **no test may
reach the network**.

What these assert that nothing else can: that a typo costs nothing, that typed and dropped
merge into one reference, and that a contig which arrived from a record comes out of the import
already linked to it.
"""

import json
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from mutint_experiment.models import Project
from mutint_import import ncbi_fetch
from mutint_import.models import STATE_FAILED, UploadSession
from mutint_import.reference import sequence_digest
from mutint_import.tests import breseq_fixture
from mutint_import.tests.test_ncbi_fetch import _Response, genbank_text, no_delay
from mutint_sample.models import DatabaseSequenceLink, ReferenceSequences

CHROMOSOME = breseq_fixture.SEQUENCE_A
PLASMID = "TTTTGGGGCCCCAAAA" * 10


def _summary(accession, slen):
    return {"result": {"uids": ["1"], "1": {"accessionversion": accession, "slen": slen}}}


class AccessionImportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="tester", email="t@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.user)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

    # --- helpers ----------------------------------------------------------------------

    def _create(self, files=(), accessions="", import_type="reference"):
        return self.client.post(
            "/import/uploads/",
            data=json.dumps({"experiment_id": self.experiment.id,
                             "import_type": import_type,
                             "files": list(files),
                             "accessions": accessions}),
            content_type="application/json")

    def _chunk(self, upload_id, path, payload):
        return self.client.post(
            "/import/uploads/%s/chunk" % upload_id,
            {"path": path, "offset": "0", "chunk": SimpleUploadedFile("chunk", payload)})

    def _finalize(self, upload_id):
        return self.client.post("/import/uploads/%s/finalize" % upload_id, {})

    def _fake_ncbi(self, summaries, genbank):
        """One patch, routed by URL.

        **`mutint_sample.ncbi.requests` and `mutint_import.ncbi_fetch.requests` are the same
        module object**, so patching `requests.get` through one name patches it through the
        other -- two patches do not give two fakes, the second simply wins. Hence one fake
        that answers according to what was asked for, which is closer to NCBI anyway.
        """
        answers = list(summaries)

        def get(url, **kwargs):
            if "esummary" in url:
                return _Response(payload=answers.pop(0) if answers else {"result":
                                                                         {"uids": []}})
            if "efetch" in url:
                return _Response(text=genbank)
            raise AssertionError("unexpected NCBI request: %s" % url)

        return mock.patch("mutint_sample.ncbi.requests.get", side_effect=get)

    def _import_chromosome(self, files=(), accessions="NC_000913.3",
                           import_type="reference"):
        fake = self._fake_ncbi([_summary("NC_000913.3", len(CHROMOSOME))],
                               genbank_text([("NC_000913.3", CHROMOSOME)]))
        with no_delay(), fake:
            created = self._create(files=[{"path": p, "size": len(b)} for p, b in files],
                                   accessions=accessions, import_type=import_type)
            self.assertEqual(created.status_code, 200, created.content)
            upload_id = created.json()["upload_id"]
            for path, payload in files:
                self.assertEqual(self._chunk(upload_id, path, payload).status_code, 200)
            return upload_id, self._finalize(upload_id)

    # --- the preflight ----------------------------------------------------------------

    def test_an_accession_alone_opens_a_session_with_no_files(self):
        """The case `build_manifest` used to refuse outright.

        A reference is the one import that may legitimately carry nothing to upload.
        """
        with mock.patch("mutint_sample.ncbi.requests.get",
                        return_value=_Response(
                            payload=_summary("NC_000913.3", len(CHROMOSOME)))):
            response = self._create(accessions="NC_000913.3")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["files"], 0)
        stored = UploadSession.objects.get().accessions
        self.assertEqual(stored[0]["typed"], "NC_000913.3")
        # The *resolved* plan, not the raw text: finalize does not get a second opinion about
        # what was meant.
        self.assertEqual(stored[0]["records"][0]["accession"], "NC_000913.3")

    def test_neither_files_nor_accessions_is_refused(self):
        response = self._create()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose files, or type an NCBI accession", response.json()["error"])

    def test_a_typo_is_refused_before_anything_is_created(self):
        """The whole reason resolving happens at create rather than at finalize.

        Caught at finalize instead, the answer would arrive after an upload, and the staged
        files would go with the failed session.
        """
        with mock.patch("mutint_sample.ncbi.requests.get",
                        return_value=_Response(payload={"result": {"uids": []}})):
            response = self._create(accessions="NC_0009133")

        self.assertEqual(response.status_code, 400)
        self.assertIn("NC_0009133", response.json()["error"])
        self.assertEqual(UploadSession.objects.count(), 0)

    def test_ncbi_is_not_asked_on_behalf_of_somebody_who_may_not_write(self):
        """The permission check has to come first, or this endpoint is an open relay.

        Anyone signed in could otherwise make the installation fetch from NCBI against an
        experiment they have no rights to.
        """
        other = User.objects.create(username="reader", email="r@e.com", is_active=True)
        other.set_password("pw")
        other.save()
        self.client.force_login(other)

        with mock.patch("mutint_sample.ncbi.requests.get") as get:
            response = self._create(accessions="NC_000913.3")

        self.assertEqual(response.status_code, 403)
        get.assert_not_called()

    def test_a_type_that_does_not_take_accessions_refuses_them_by_name(self):
        with mock.patch("mutint_sample.ncbi.requests.get") as get:
            response = self._create(files=[{"path": "a.gd", "size": 1}],
                                    accessions="NC_000913.3",
                                    import_type="breseq_folder")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not take NCBI accessions", response.json()["error"])
        get.assert_not_called()

    def test_a_dropped_file_may_not_sit_where_a_download_will_go(self):
        with mock.patch("mutint_sample.ncbi.requests.get",
                        return_value=_Response(
                            payload=_summary("NC_000913.3", len(CHROMOSOME)))):
            response = self._create(
                files=[{"path": "%s/NC_000913.3.gbk" % ncbi_fetch.STAGED_SUBDIR, "size": 1}],
                accessions="NC_000913.3")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Rename it", response.json()["error"])

    def test_a_folder_called_ncbi_is_ordinary_when_no_accession_was_typed(self):
        # Nothing writes into that directory unless something is being downloaded, so the
        # reservation must not outlive the accessions that justify it.
        response = self._create(
            files=[{"path": "%s/notes.fasta" % ncbi_fetch.STAGED_SUBDIR, "size": 1}])
        self.assertEqual(response.status_code, 200, response.content)

    # --- importing --------------------------------------------------------------------

    def test_an_accession_alone_establishes_the_reference(self):
        _upload_id, response = self._import_chromosome()

        self.assertEqual(response.status_code, 200, response.content)
        summary = response.json()
        self.assertTrue(summary["has_reference"])
        self.assertEqual(ReferenceSequences.objects.count(), 1)
        # Reported as an ordinary file row, named for where it was staged.
        row = summary["files"][0]
        self.assertIsNone(row["error"])
        self.assertEqual(row["kind"], "reference")
        self.assertIn("NC_000913.3.gbk", row["file"])

    def test_the_contig_arrives_already_linked_to_the_record_it_came_from(self):
        """The point of the feature: no Check button, no second step.

        The name the contig is imported under is its LOCUS (`NC_000913`), while the link is
        filed under the VERSION (`NC_000913.3`) -- the two are tied together by the digest,
        which is what a DatabaseSequenceLink is keyed on.
        """
        self._import_chromosome()

        link = DatabaseSequenceLink.objects.get()
        self.assertEqual(link.status, DatabaseSequenceLink.VERIFIED)
        self.assertEqual(link.accession, "NC_000913.3")
        self.assertEqual(link.sha256, sequence_digest(CHROMOSOME))
        self.assertEqual(link.proposed_by, self.user)
        self.assertIn("Downloaded from NCBI", link.detail)

        # And the Reference page therefore shows it confirmed, with nobody having checked it.
        from mutint_sample import ncbi

        self.assertIn("NC_000913",
                      ncbi.verified_contig_names(self.experiment))

    def test_a_typed_chromosome_and_a_dropped_plasmid_are_one_reference(self):
        """The headline requirement: accessions and files merge.

        Nothing in the reference handler knows an accession was involved -- by the time it
        runs, the download is one more file in the staging tree.
        """
        plasmid = breseq_fixture.fasta_text([("plasmid", PLASMID)]).encode()
        _upload_id, response = self._import_chromosome(
            files=[("plasmid.fasta", plasmid)])

        self.assertEqual(response.status_code, 200, response.content)
        reference = ReferenceSequences.objects.get()
        self.assertEqual(sorted(entry["id"] for entry in reference.seq_ids),
                         ["NC_000913", "plasmid"])
        # Both files are rows; only the contig that came from NCBI is linked.
        self.assertEqual(len(response.json()["files"]), 2)
        self.assertEqual([link.accession for link in DatabaseSequenceLink.objects.all()],
                         ["NC_000913.3"])

    def test_a_contig_that_was_dropped_rather_than_downloaded_is_not_linked(self):
        # The gate is `seq_ids`: a digest the reference did not end up holding has nothing to
        # be recorded about.
        plasmid = breseq_fixture.fasta_text([("plasmid", PLASMID)]).encode()
        self._import_chromosome(files=[("plasmid.fasta", plasmid)])
        self.assertFalse(
            DatabaseSequenceLink.objects.filter(sha256=sequence_digest(PLASMID)).exists())

    def test_update_annotation_by_accession_records_the_link_too(self):
        """The same question at the other moment, and it needed no code of its own."""
        # Establish the genome from a bare FASTA first, so there is no link yet.
        fasta = breseq_fixture.fasta_text([("NC_000913", CHROMOSOME)]).encode()
        created = self._create(files=[{"path": "ref.fasta", "size": len(fasta)}])
        upload_id = created.json()["upload_id"]
        self._chunk(upload_id, "ref.fasta", fasta)
        self.assertEqual(self._finalize(upload_id).status_code, 200)
        self.assertEqual(DatabaseSequenceLink.objects.count(), 0)

        _upload_id, response = self._import_chromosome(import_type="replace_annotation")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(DatabaseSequenceLink.objects.get().accession, "NC_000913.3")

    def test_an_accession_that_is_not_this_experiments_genome_links_nothing(self):
        """A `replace_annotation` against the wrong genome is refused by the existing check.

        Nothing new had to refuse it -- and because the digest never reaches `seq_ids`, no
        link is recorded either.
        """
        fasta = breseq_fixture.fasta_text([("NC_000913", CHROMOSOME)]).encode()
        created = self._create(files=[{"path": "ref.fasta", "size": len(fasta)}])
        upload_id = created.json()["upload_id"]
        self._chunk(upload_id, "ref.fasta", fasta)
        self._finalize(upload_id)

        other = "GGGGCCCCTTTTAAAA" * 12
        fake = self._fake_ncbi([_summary("NC_999999.1", len(other))],
                               genbank_text([("NC_999999.1", other)]))
        with no_delay(), fake:
            created = self._create(accessions="NC_999999.1",
                                   import_type="replace_annotation")
            response = self._finalize(created.json()["upload_id"])

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("not this experiment's reference",
                      response.json()["files"][0]["error"])
        self.assertEqual(DatabaseSequenceLink.objects.count(), 0)

    def test_a_download_that_fails_fails_the_session_and_says_why(self):
        summary_patch = mock.patch(
            "mutint_sample.ncbi.requests.get",
            return_value=_Response(payload=_summary("NC_000913.3", len(CHROMOSOME))))
        with summary_patch:
            created = self._create(accessions="NC_000913.3")
        upload_id = created.json()["upload_id"]

        with mock.patch("mutint_import.ncbi_fetch.requests.get",
                        return_value=_Response(status_code=503)):
            response = self._finalize(upload_id)

        self.assertEqual(response.status_code, 502)
        self.assertIn("NCBI answered HTTP 503", response.json()["error"])
        self.assertEqual(UploadSession.objects.get(pk=upload_id).state, STATE_FAILED)
        self.assertEqual(ReferenceSequences.objects.count(), 0)

    def test_the_download_says_what_it_is_doing_while_it_runs(self):
        """The window the progress bar would otherwise have nothing to say about.

        No unit has been announced yet at that point, so the page has no table to draw -- the
        stage line is the only thing standing between a genome download and a page that looks
        stuck.
        """
        self._import_chromosome()
        # `finish()` clears the stage, so the assertion is that one was written at all: the
        # snapshot is a picture of the present, not a log.
        session = UploadSession.objects.get()
        self.assertEqual(session.progress["stage"], "")

        recorded = []
        with mock.patch("mutint_common.import_progress.stage",
                        side_effect=lambda message: recorded.append(message)):
            with no_delay(), mock.patch(
                    "mutint_import.ncbi_fetch.requests.get",
                    return_value=_Response(
                        text=genbank_text([("NC_000913.3", CHROMOSOME)]))):
                ncbi_fetch.download(
                    tempfile.mkdtemp(),
                    [ncbi_fetch.Plan("NC_000913.3", "nucleotide",
                                     [{"accession": "NC_000913.3",
                                       "length": len(CHROMOSOME)}])],
                    report=lambda message: recorded.append(message))
        self.assertIn("Downloading NC_000913.3 from NCBI…", recorded)

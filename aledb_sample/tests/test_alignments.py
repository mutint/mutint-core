import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_sample.models import Sample

PAYLOAD = bytes(range(256)) * 8  # 2048 bytes, non-repeating within each 256-byte block


class AlignmentServingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", email="t@e.com", is_active=True, is_staff=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "s1", bam_bytes=PAYLOAD)
        breseq_folder.import_breseq_folders(
            self.drop, project_name="p", experiment_name="e", person="tester")
        self.reseq = Sample.objects.get()
        self.experiment = self.reseq.experiment
        self.bam_url = "/mutations/alignments/%d/bam" % self.reseq.id

    def _body(self, response):
        return b"".join(response.streaming_content)

    # --- whole-file ---------------------------------------------------------------------

    def test_full_get_returns_the_bam_and_advertises_ranges(self):
        response = self.client.get(self.bam_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(int(response["Content-Length"]), len(PAYLOAD))
        self.assertEqual(self._body(response), PAYLOAD)

    def test_bai_and_reference_files_are_served(self):
        for url in ("/mutations/alignments/%d/bai" % self.reseq.id,
                    "/mutations/reference/%d/fasta" % self.experiment.id,
                    "/mutations/reference/%d/fai" % self.experiment.id,
                    "/mutations/reference/%d/gff3" % self.experiment.id):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertTrue(self._body(response), url)

    # --- range requests, which a genome browser depends on -------------------------------

    def test_byte_range_returns_206_with_exact_slice(self):
        response = self.client.get(self.bam_url, HTTP_RANGE="bytes=0-99")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], "bytes 0-99/%d" % len(PAYLOAD))
        self.assertEqual(int(response["Content-Length"]), 100)
        body = self._body(response)
        self.assertEqual(len(body), 100)
        self.assertEqual(body, PAYLOAD[0:100])

    def test_mid_file_range(self):
        response = self.client.get(self.bam_url, HTTP_RANGE="bytes=1000-1099")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(self._body(response), PAYLOAD[1000:1100])

    def test_open_ended_range_runs_to_the_end(self):
        start = len(PAYLOAD) - 10
        response = self.client.get(self.bam_url, HTTP_RANGE="bytes=%d-" % start)

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"],
                         "bytes %d-%d/%d" % (start, len(PAYLOAD) - 1, len(PAYLOAD)))
        self.assertEqual(self._body(response), PAYLOAD[start:])

    def test_suffix_range_returns_the_final_bytes(self):
        response = self.client.get(self.bam_url, HTTP_RANGE="bytes=-16")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(self._body(response), PAYLOAD[-16:])

    def test_range_past_the_end_of_file_is_416(self):
        response = self.client.get(
            self.bam_url, HTTP_RANGE="bytes=%d-" % (len(PAYLOAD) + 10))

        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], "bytes */%d" % len(PAYLOAD))

    def test_unparseable_range_is_416(self):
        response = self.client.get(self.bam_url, HTTP_RANGE="rows=1-2")
        self.assertEqual(response.status_code, 416)

    # --- authorization and missing files -------------------------------------------------

    def test_user_without_project_access_is_forbidden(self):
        outsider = User.objects.create(
            username="outsider", email="o@e.com", is_active=True, is_staff=False)
        outsider.set_password("pw")
        outsider.save()
        self.client.force_login(outsider)

        response = self.client.get(self.bam_url)
        self.assertEqual(response.status_code, 403)

    def test_unknown_sample_is_404(self):
        response = self.client.get("/mutations/alignments/999999/bam")
        self.assertEqual(response.status_code, 404)

    def test_sample_without_a_stored_bam_is_404(self):
        """A bare-.gd import has no alignment; the route must 404, not 500."""
        reseq = Sample.objects.create(
            time_point=self.reseq.time_point, source_name="no-bam", person="tester")
        response = self.client.get("/mutations/alignments/%d/bam" % reseq.id)
        self.assertEqual(response.status_code, 404)

    def test_no_client_path_reaches_the_filesystem(self):
        """The route takes a pk, so a traversal attempt cannot even match the URL."""
        response = self.client.get("/mutations/alignments/..%2F..%2Fetc%2Fpasswd/bam")
        self.assertEqual(response.status_code, 404)


class ChromAliasTestCase(TestCase):
    """The alias table that lets a renamed contig keep its stored alignments.

    Renaming an experiment's sequences renames the reference and the mutations but leaves
    every stored BAM and coverage BigWig carrying the names it was built with. igv resolves
    the difference from this table -- verified against a real BAM through igv's own reader,
    where the renamed reference drew pixel-for-pixel what the un-renamed one did.
    """

    def test_an_experiment_that_was_never_renamed_gets_no_table(self):
        from aledb_sample.views.alignments import chromalias_text

        self.assertEqual("", chromalias_text([{"id": "REL606", "length": 4629812}]))

    def test_the_current_name_comes_first(self):
        """igv takes as canonical whichever field is in the genome's chromosomeNames and
        falls back to field 0, so the order is not cosmetic."""
        from aledb_sample.views.alignments import chromalias_text

        text = chromalias_text(
            [{"id": "NC_012967.1", "length": 4629812, "aliases": ["REL606"]}])

        self.assertEqual("#name\tprevious\nNC_012967.1\tREL606\n", text)

    def test_every_previous_name_gets_a_row(self):
        """A sequence renamed twice must not strand the first name: a BAM stored before
        either rename still carries it."""
        from aledb_sample.views.alignments import chromalias_text

        text = chromalias_text([{"id": "c", "length": 10, "aliases": ["a", "b"]}])

        self.assertEqual(["#name\tprevious", "c\ta", "c\tb"], text.strip().split("\n"))

    def test_only_renamed_sequences_appear(self):
        from aledb_sample.views.alignments import chromalias_text

        text = chromalias_text([{"id": "plasmid", "length": 5},
                                {"id": "chr", "length": 10, "aliases": ["old"]}])

        self.assertEqual(["#name\tprevious", "chr\told"], text.strip().split("\n"))


class EcocycAccessionTestCase(TestCase):
    """Renaming a contig must not silently switch EcoCyc links off.

    `is_ecocyc_gene` compared `reseq_reference` to 'NC_000913' exactly, which was safe only
    while a contig name could never change. Re-establishing a reference from a RefSeq
    download names it NC_000913.3, and every gene link would have gone quiet.
    """

    def _mutation(self, name):
        from aledb_sample.models import Mutation

        return Mutation(reseq_reference=name)

    def test_the_bare_accession_is_ecocyc(self):
        self.assertTrue(self._mutation("NC_000913").is_ecocyc_gene())

    def test_a_versioned_accession_is_ecocyc(self):
        self.assertTrue(self._mutation("NC_000913.3").is_ecocyc_gene())

    def test_another_genome_is_not(self):
        for name in ("REL606", "NC_012967.1", "", None, "NC_0009134"):
            self.assertFalse(self._mutation(name).is_ecocyc_gene(), name)

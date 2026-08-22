import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_seq.models import ResequencingExperiment

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
        self.reseq = ResequencingExperiment.objects.get()
        self.experiment = self.reseq.ale_experiment
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
                    "/mutations/reference/%d/fasta" % self.experiment.ale_id,
                    "/mutations/reference/%d/fai" % self.experiment.ale_id,
                    "/mutations/reference/%d/gff3" % self.experiment.ale_id):
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
        reseq = ResequencingExperiment.objects.create(
            tech_rep=self.reseq.tech_rep, sample_name="no-bam", person="tester")
        response = self.client.get("/mutations/alignments/%d/bam" % reseq.id)
        self.assertEqual(response.status_code, 404)

    def test_no_client_path_reaches_the_filesystem(self):
        """The route takes a pk, so a traversal attempt cannot even match the URL."""
        response = self.client.get("/mutations/alignments/..%2F..%2Fetc%2Fpasswd/bam")
        self.assertEqual(response.status_code, 404)

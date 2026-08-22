"""The /aledata/ route: containment, ownership, authorization, and range support.

Every case here is a defect the old `protected_file_serve` had. It concatenated
`DOC_ROOT + page_name`, had a `.html`/`.ba` branch that skipped permissions entirely, and
returned None from its 404 path (a 500, not a 404).
"""

import os
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_experiment.permissions import grant_access_to_project
from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_seq.models import ResequencingExperiment

PAYLOAD = bytes(range(256)) * 8  # 2048 bytes


class AledataServingTestCase(TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        # The view reads DOC_ROOT at import time, so override the module attribute rather
        # than the setting.
        from aledb_common import views as common_views
        common_views.DOC_ROOT = self.root
        self.addCleanup(setattr, common_views, "DOC_ROOT", settings.ALE_DATA_ROOT_DIR)

        # try_creating_project -> find_user prompts on stdin for an unknown name.
        self.owner = User.objects.create(
            username="tester", email="t@e.com", is_active=True)
        self.owner.set_password("pw")
        self.owner.save()

        # Build a real sample through the import path rather than hand-assembling the
        # AleId/Flask/Isolate/TechnicalReplicate chain.
        drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, drop, True)
        breseq_fixture.write_sample(drop, "3-72-1-1")
        breseq_folder.import_breseq_folders(
            drop, project_name="P", experiment_name="e", person="tester")

        self.reseq = ResequencingExperiment.objects.get()
        # Point it at the legacy data-root layout /aledata/ serves.
        self.reseq.location = "exp1/breseq/3-72-1-1/output/"
        self.reseq.gatk_location = "exp1/gatk/3-72-1-1/"
        self.reseq.experiment_location = "exp1"
        self.reseq.save(
            update_fields=["location", "gatk_location", "experiment_location"])

        self.experiment = self.reseq.ale_experiment
        self.project = self.experiment.project
        self.project.is_public = False
        self.project.save(update_fields=["is_public"])
        grant_access_to_project(self.project, [self.owner])

        self.report = "exp1/breseq/3-72-1-1/output/index.html"
        self._write(self.report, b"<html>report</html>")
        self._write("exp1/breseq/3-72-1-1/output/alignment.bam", PAYLOAD)
        self._write("exp1/gatk/3-72-1-1/coverage.png", b"\x89PNG fake")

        # A file inside the root that belongs to no experiment.
        self._write("orphan/notes.html", b"<html>orphan</html>")

        # The traversal target, written outside the root.
        self.outside = os.path.join(os.path.dirname(self.root), "outside_secret.html")
        with open(self.outside, "wb") as handle:
            handle.write(b"secret")
        self.addCleanup(
            lambda: os.path.exists(self.outside) and os.remove(self.outside))

    def _write(self, relative, content):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def _body(self, response):
        return b"".join(response.streaming_content)

    # --- containment --------------------------------------------------------------------

    def test_traversal_out_of_the_data_root_is_refused(self):
        for attempt in ("../outside_secret.html",
                        "../../../../etc/passwd.html",
                        "exp1/../../outside_secret.html"):
            response = self.client.get("/aledata/" + attempt)
            self.assertEqual(response.status_code, 404, attempt)

    def test_a_missing_file_is_a_404_not_a_500(self):
        self.client.force_login(self.owner)
        response = self.client.get("/aledata/exp1/breseq/3-72-1-1/output/nope.html")
        self.assertEqual(response.status_code, 404)

    def test_a_file_no_experiment_owns_is_a_404(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/aledata/orphan/notes.html").status_code, 404)

    # --- authorization ------------------------------------------------------------------

    def test_html_no_longer_bypasses_the_permission_check(self):
        """The old `.html` branch served this to anyone, checking nothing."""
        response = self.client.get("/aledata/" + self.report)
        self.assertEqual(response.status_code, 403)

    def test_bam_no_longer_bypasses_the_permission_check(self):
        """`.ba` was a substring test, so every .bam and .bai landed in the same branch."""
        response = self.client.get(
            "/aledata/exp1/breseq/3-72-1-1/output/alignment.bam")
        self.assertEqual(response.status_code, 403)

    def test_a_stranger_is_forbidden(self):
        stranger = User.objects.create(username="s", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        self.assertEqual(self.client.get("/aledata/" + self.report).status_code, 403)

    def test_the_owner_may_read_their_experiments_files(self):
        self.client.force_login(self.owner)
        response = self.client.get("/aledata/" + self.report)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response), b"<html>report</html>")
        self.assertTrue(response["Content-Type"].startswith("text/html"))

    def test_a_public_project_is_readable_anonymously(self):
        self.project.is_public = True
        self.project.save(update_fields=["is_public"])

        response = self.client.get("/aledata/" + self.report)
        self.assertEqual(response.status_code, 200)

    def test_gatk_paths_resolve_through_gatk_location(self):
        self.client.force_login(self.owner)
        response = self.client.get("/aledata/exp1/gatk/3-72-1-1/coverage.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_a_directory_serves_its_index(self):
        self.client.force_login(self.owner)
        response = self.client.get("/aledata/exp1/breseq/3-72-1-1/output/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response), b"<html>report</html>")

    # --- streaming ----------------------------------------------------------------------

    def test_range_requests_are_supported(self):
        self.client.force_login(self.owner)
        url = "/aledata/exp1/breseq/3-72-1-1/output/alignment.bam"

        full = self.client.get(url)
        self.assertEqual(full["Accept-Ranges"], "bytes")
        self.assertEqual(int(full["Content-Length"]), len(PAYLOAD))

        partial = self.client.get(url, HTTP_RANGE="bytes=10-19")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial["Content-Range"],
                         "bytes 10-19/%d" % len(PAYLOAD))
        self.assertEqual(self._body(partial), PAYLOAD[10:20])

    def test_an_unsatisfiable_range_is_a_416(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            "/aledata/exp1/breseq/3-72-1-1/output/alignment.bam",
            HTTP_RANGE="bytes=99999-")

        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], "bytes */%d" % len(PAYLOAD))

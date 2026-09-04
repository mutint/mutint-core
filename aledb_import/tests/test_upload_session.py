import json
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_import import breseq_folder
from aledb_import.models import STATE_FINALIZED, UploadSession
from aledb_import.tests import breseq_fixture
from aledb_import.upload_session import UploadError, sanitize_relative_path
from aledb_experiment.models import Project
from aledb_seq.models import ExperimentReference, Sample


class SanitizePathTestCase(TestCase):
    def test_accepts_ordinary_relative_paths(self):
        self.assertEqual(
            sanitize_relative_path("s1/data/output.gd"),
            os.path.join("s1", "data", "output.gd"))

    def test_normalizes_backslashes_and_dot_segments(self):
        self.assertEqual(
            sanitize_relative_path("s1\\data\\./reference.bam"),
            os.path.join("s1", "data", "reference.bam"))

    def test_rejects_traversal_and_absolute_paths(self):
        for bad in ("../etc/passwd",
                    "s1/../../etc/passwd",
                    "/etc/passwd",
                    "C:/Windows/system.ini",
                    "",
                    "./",
                    None):
            with self.assertRaises(UploadError, msg="accepted %r" % (bad,)):
                sanitize_relative_path(bad)


class UploadSessionEndpointTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User",
            email="t@e.com", is_active=True, is_staff=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        # Uploads target an experiment by primary key and require edit rights on its project.
        self.project = Project.objects.create(name="p", user=self.user)
        from aledb_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

    def _create(self, files, experiment_id=None, import_type="genomediff"):
        """import_type is required now, so the helper supplies one by default --
        auto-detect used to be the fallback and is gone."""
        return self.client.post(
            "/import/uploads/",
            data=json.dumps({
                "ale_experiment_id": (self.experiment.id if experiment_id is None
                                      else experiment_id),
                "import_type": import_type,
                "files": files}),
            content_type="application/json")

    def _chunk(self, upload_id, path, offset, payload):
        return self.client.post(
            "/import/uploads/%s/chunk" % upload_id,
            {"path": path, "offset": str(offset),
             "chunk": SimpleUploadedFile("chunk", payload)})

    # --- session lifecycle -------------------------------------------------------------

    def test_create_requires_a_known_experiment(self):
        self.assertEqual(
            self._create([{"path": "a.gd", "size": 1}], experiment_id=999999).status_code,
            404)

    def test_create_rejects_an_unknown_import_type(self):
        response = self._create([{"path": "a.gd", "size": 1}], import_type="nope")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown import type", response.json()["error"])

    def test_cannot_upload_into_someone_elses_experiment(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        stranger.set_password("pw")
        stranger.save()
        self.client.force_login(stranger)

        response = self._create([{"path": "a.gd", "size": 1}])
        self.assertEqual(response.status_code, 403)

    def test_create_rejects_a_traversal_path_in_the_manifest(self):
        response = self._create([{"path": "../escape.gd", "size": 1}])
        self.assertEqual(response.status_code, 400)
        self.assertIn("..", response.json()["error"])
        self.assertEqual(UploadSession.objects.count(), 0)

    def test_create_returns_an_id_and_declared_total(self):
        response = self._create([{"path": "s1/data/output.gd", "size": 10},
                                 {"path": "s1/data/reference.bam", "size": 90}])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["files"], 2)
        self.assertEqual(body["declared_bytes"], 100)
        self.assertTrue(UploadSession.objects.filter(pk=body["upload_id"]).exists())

    # --- chunking ----------------------------------------------------------------------

    def test_chunks_assemble_byte_identical_content(self):
        payload = bytes(range(256)) * 40  # 10 KB
        upload_id = self._create(
            [{"path": "s1/data/reference.bam", "size": len(payload)}]).json()["upload_id"]

        step = 1024
        for offset in range(0, len(payload), step):
            response = self._chunk(upload_id, "s1/data/reference.bam", offset,
                                   payload[offset:offset + step])
            self.assertEqual(response.status_code, 200)

        staged = os.path.join(store.staging_dir(upload_id), "s1", "data", "reference.bam")
        with open(staged, "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_retried_chunk_is_not_double_counted(self):
        payload = b"A" * 512
        upload_id = self._create(
            [{"path": "s1/data/reference.bam", "size": len(payload)}]).json()["upload_id"]

        self._chunk(upload_id, "s1/data/reference.bam", 0, payload)
        # Same chunk again, as a client retry after a dropped connection.
        response = self._chunk(upload_id, "s1/data/reference.bam", 0, payload)
        self.assertEqual(response.status_code, 200)

        session = UploadSession.objects.get(pk=upload_id)
        self.assertEqual(session.received_bytes, len(payload))
        self.assertEqual(response.json()["size"], len(payload))

    def test_out_of_order_chunk_is_refused_with_the_expected_offset(self):
        upload_id = self._create(
            [{"path": "s1/data/reference.bam", "size": 100}]).json()["upload_id"]

        self._chunk(upload_id, "s1/data/reference.bam", 0, b"A" * 10)
        response = self._chunk(upload_id, "s1/data/reference.bam", 50, b"B" * 10)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["expected_offset"], 10)

    def test_chunk_path_traversal_is_refused(self):
        upload_id = self._create(
            [{"path": "s1/data/output.gd", "size": 4}]).json()["upload_id"]

        response = self._chunk(upload_id, "../../escaped.gd", 0, b"data")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            os.path.exists(os.path.join(os.path.dirname(self.store), "escaped.gd")))

    def test_another_user_cannot_write_into_the_session(self):
        upload_id = self._create(
            [{"path": "s1/data/output.gd", "size": 4}]).json()["upload_id"]

        other = User.objects.create(username="other", email="o@e.com", is_active=True)
        other.set_password("pw")
        other.save()
        self.client.force_login(other)

        response = self._chunk(upload_id, "s1/data/output.gd", 0, b"data")
        self.assertEqual(response.status_code, 403)

    def test_unknown_session_is_404(self):
        response = self._chunk("00000000-0000-4000-8000-000000000000",
                               "s1/data/output.gd", 0, b"data")
        self.assertEqual(response.status_code, 404)

    # --- finalize ----------------------------------------------------------------------

    def _upload_sample_folder(self, sample_name="s1", **fixture_kwargs):
        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        breseq_fixture.write_sample(source, sample_name, **fixture_kwargs)

        entries = []
        for relative in breseq_folder.SAMPLE_FILES:
            path = os.path.join(source, sample_name, relative)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                payload = handle.read()
            entries.append((sample_name + "/" + relative.replace(os.sep, "/"), payload))

        created = self._create([{"path": p, "size": len(b)} for p, b in entries],
                               import_type="breseq_folder")
        self.assertEqual(created.status_code, 200, created.content)
        upload_id = created.json()["upload_id"]
        for path, payload in entries:
            response = self._chunk(upload_id, path, 0, payload)
            self.assertEqual(response.status_code, 200, response.content)
        return upload_id

    def test_finalize_imports_and_clears_staging(self):
        upload_id = self._upload_sample_folder()
        response = self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        self.assertEqual(response.status_code, 200, response.content)
        summary = response.json()
        self.assertIsNone(summary["files"][0]["error"])
        self.assertGreater(summary["total_mutations"], 0)

        reseq = Sample.objects.get()
        self.assertTrue(reseq.bam_stored)
        self.assertTrue(os.path.isfile(store.sample_path(reseq.id, store.SAMPLE_BAM)))
        self.assertEqual(ExperimentReference.objects.count(), 1)

        self.assertFalse(os.path.exists(store.staging_dir(upload_id)),
                         "staging directory should be removed on finalize")
        self.assertEqual(UploadSession.objects.get(pk=upload_id).state, STATE_FINALIZED)

    def test_finalize_reports_whether_a_reference_now_exists(self):
        """The Add page reloads on this, so its menu stops offering the pre-reference
        choices. A breseq folder brings its own reference, so it flips the flag."""
        upload_id = self._upload_sample_folder()
        response = self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        self.assertTrue(response.json()["has_reference"])

    def test_a_finalized_session_cannot_be_reused(self):
        upload_id = self._upload_sample_folder()
        self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        response = self._chunk(upload_id, "s1/data/output.gd", 0, b"data")
        self.assertEqual(response.status_code, 409)

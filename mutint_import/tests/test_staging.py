"""Staging opened for a component rather than for the import registry.

The interesting cases are all about the *boundary*: that a component's session cannot be
routed through `run_import`, that one component cannot reach another's, and that claiming
takes the directory out of the reaper's hands. The chunk endpoint is deliberately not
re-tested here -- it is unchanged and `test_upload_session` already pins it -- except once,
to establish that it really does serve both kinds of session.
"""

import json
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from mutint_common import store
from mutint_experiment.models import Project
from mutint_import import staging
from mutint_import.models import (
    STATE_CLAIMED,
    STATE_FAILED,
    STATE_FINALIZED,
    STATE_OPEN,
    UploadSession,
)
from mutint_import.upload_session import UploadError, reap_expired_sessions

# An installed app label that is not mutint_import, standing in for a plugin. Any first-party
# app would do; what matters is that `apps.is_installed` says yes.
CONSUMER = "mutint_stats"


class StagingTestCase(TestCase):
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

    def _create(self, files=None, consumer=CONSUMER, experiment_id=None):
        return self.client.post(
            "/import/staging/",
            data=json.dumps({
                "experiment_id": (self.experiment.id if experiment_id is None
                                  else experiment_id),
                "consumer": consumer,
                "files": files if files is not None else [{"path": "r1.fastq", "size": 4}]}),
            content_type="application/json")

    # --- opening one --------------------------------------------------------------------

    def test_a_component_can_open_a_session(self):
        response = self._create()
        self.assertEqual(response.status_code, 200)
        session = UploadSession.objects.get(pk=response.json()["upload_id"])
        self.assertEqual(session.consumer, CONSUMER)
        self.assertEqual(session.import_type, "")
        self.assertEqual(session.state, STATE_OPEN)
        self.assertTrue(os.path.isdir(store.staging_dir(session.id)))

    def test_the_consumer_must_be_an_installed_app(self):
        response = self._create(consumer="not_an_app")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown consumer", response.json()["error"])

    def test_opening_needs_signing_in(self):
        # Not redundant with the permission check below, which anonymous also fails: this
        # states the rule where somebody editing the file will read it, the way
        # `project_create` does. The shape that has bitten this codebase is a new endpoint
        # whose author had no object to run a predicate against.
        self.client.logout()
        response = self._create()
        self.assertEqual(response.status_code, 403)
        self.assertIn("signed in", response.json()["error"])

    def test_opening_needs_edit_access(self):
        other = User.objects.create(username="reader", email="r@e.com", is_active=True)
        other.set_password("pw")
        other.save()
        self.client.force_login(other)
        self.assertEqual(self._create().status_code, 403)

    def test_a_locked_experiment_refuses(self):
        # can_edit_experiment, not can_edit_project: what a component does with a drop is a
        # write, and the lock lives on the experiment.
        self.experiment.lock(self.user)
        self.assertEqual(self._create().status_code, 403)

    def test_the_manifest_is_sanitized(self):
        response = self._create(files=[{"path": "../../etc/passwd", "size": 1}])
        self.assertEqual(response.status_code, 400)
        self.assertIn("..", response.json()["error"])

    def test_open_session_refuses_an_uninstalled_consumer(self):
        # A ValueError rather than an UploadError: this one is the component's bug, not the
        # client's, so it must not become a 400 somebody tries to fix by retyping.
        with self.assertRaises(ValueError):
            staging.open_session(self.user, self.experiment, "nope", [{"path": "a", "size": 1}])

    # --- the boundary with ordinary imports ---------------------------------------------

    def test_the_chunk_endpoint_serves_a_component_session(self):
        upload_id = self._create().json()["upload_id"]
        response = self.client.post(
            "/import/uploads/%s/chunk" % upload_id,
            {"path": "r1.fastq", "offset": "0",
             "chunk": SimpleUploadedFile("chunk", b"ACGT")})
        self.assertEqual(response.status_code, 200)
        with open(os.path.join(store.staging_dir(upload_id), "r1.fastq"), "rb") as handle:
            self.assertEqual(handle.read(), b"ACGT")

    def test_finalize_refuses_a_component_session(self):
        # Without this the session falls through to auto-detect and whichever registered
        # handler claims the suffix takes the files -- a wrong import rather than an error.
        upload_id = self._create().json()["upload_id"]
        response = self.client.post("/import/uploads/%s/finalize" % upload_id,
                                    data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertIn(CONSUMER, response.json()["error"])

    def test_session_for_refuses_another_components_session(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])

        class _Request:
            user = self.user

        found, error = staging.session_for(_Request(), session.id, "mutint_export")
        self.assertIsNone(found)
        self.assertEqual(error.status_code, 409)

    def test_session_for_refuses_somebody_elses(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        other = User.objects.create(username="other", email="o@e.com", is_active=True)

        class _Request:
            user = other

        found, error = staging.session_for(_Request(), session.id, CONSUMER)
        self.assertIsNone(found)
        self.assertEqual(error.status_code, 403)

    # --- claiming, and what it means ----------------------------------------------------

    def test_claiming_returns_the_root_and_marks_the_session(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        root = staging.claim(session)
        self.assertEqual(root, store.staging_dir(session.id))
        self.assertEqual(
            UploadSession.objects.get(pk=session.id).state, STATE_CLAIMED)

    def test_claiming_twice_is_allowed(self):
        # A retried launch must not fail on its own earlier attempt.
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        staging.claim(session)
        self.assertEqual(staging.claim(session), store.staging_dir(session.id))

    def test_claiming_a_spent_session_refuses(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        staging.abandon(session)
        with self.assertRaises(UploadError):
            staging.claim(session)

    def test_the_reaper_leaves_a_claimed_session_alone(self):
        # The whole point of the state. A component's work outlives the TTL by design -- a
        # breseq run is hours -- so the reaper must not delete the files out from under it.
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        root = staging.claim(session)
        os.makedirs(root, exist_ok=True)
        UploadSession.objects.filter(pk=session.id).update(
            updated=timezone.now() - timezone.timedelta(days=30))

        self.assertEqual(reap_expired_sessions(), 0)
        self.assertTrue(os.path.isdir(root))

    def test_the_reaper_still_takes_an_abandoned_component_session(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        root = store.staging_dir(session.id)
        UploadSession.objects.filter(pk=session.id).update(
            updated=timezone.now() - timezone.timedelta(days=30))

        self.assertEqual(reap_expired_sessions(), 1)
        self.assertFalse(os.path.isdir(root))

    def test_close_removes_the_files(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        root = staging.claim(session)
        staging.close(session)
        self.assertFalse(os.path.isdir(root))
        self.assertEqual(
            UploadSession.objects.get(pk=session.id).state, STATE_FINALIZED)

    def test_abandon_removes_the_files(self):
        session = staging.open_session(
            self.user, self.experiment, CONSUMER, [{"path": "r1.fastq", "size": 4}])
        root = store.staging_dir(session.id)
        staging.abandon(session)
        self.assertFalse(os.path.isdir(root))
        self.assertEqual(UploadSession.objects.get(pk=session.id).state, STATE_FAILED)


class ComponentDirTestCase(TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def test_it_is_under_the_store(self):
        self.assertEqual(
            store.component_dir("mutint_breseq", 7),
            os.path.join(self.store, "components", "mutint_breseq", "7"))

    def test_a_key_that_is_not_a_number_is_refused(self):
        for bad in ("../..", "7/../..", "", None):
            with self.assertRaises((ValueError, TypeError), msg="accepted %r" % (bad,)):
                store.component_dir("mutint_breseq", bad)

    def test_a_component_name_with_a_separator_is_refused(self):
        for bad in ("../etc", "a/b", "a.b", ""):
            with self.assertRaises(ValueError, msg="accepted %r" % (bad,)):
                store.component_dir(bad, 1)

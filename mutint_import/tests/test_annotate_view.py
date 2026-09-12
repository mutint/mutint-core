"""`POST /import/annotate`: the Run annotators button, with nothing uploaded."""

import json
import os
import shutil
import tempfile

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from mutint_common.annotator_registry import (
    register_reference_annotator,
    unregister_reference_annotator,
)
from mutint_experiment.models import Project
from mutint_import import import_lock, reference, reference_store

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "annotate", "tests", "fixtures")
SYNTHETIC_GFF3 = os.path.join(FIXTURES, "synthetic.gff3")


class AnnotateViewTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.project = Project.objects.create(name="p", user=self.user)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

        self.seen = []
        register_reference_annotator(
            apps.get_app_config("mutint_import"), name="t_view", label="View Thing",
            run=lambda e, o, u: self.seen.append((e.pk, o, u.username)) or
                {"message": "queued as job 1"},
            template="tests/annotator_panel.html",
            clean=lambda o: {"depth": int(o.get("depth", 1))})
        self.addCleanup(unregister_reference_annotator, "t_view")

    def _establish(self):
        gff3_text, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences,
                                           update_annotation=True)

    def _post(self, body):
        return self.client.post("/import/annotate", data=json.dumps(body),
                                content_type="application/json")

    def test_runs_the_ticked_annotators_against_the_stored_reference(self):
        self._establish()
        response = self._post({"experiment_id": self.experiment.id,
                               "annotators": {"t_view": {"enabled": True, "depth": "4"}}})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["experiment_id"], self.experiment.id)
        self.assertEqual([(self.experiment.pk, {"depth": 4}, "owner")], self.seen)
        self.assertEqual(body["annotators"][0]["label"], "View Thing")
        self.assertEqual(body["annotators"][0]["message"], "queued as job 1")
        self.assertIsNone(body["annotators"][0]["error"])
        # And the lock is not left behind.
        self.assertIsNone(import_lock.current())

    def test_an_unknown_experiment_is_a_404(self):
        self.assertEqual(self._post({"experiment_id": 999999, "annotators": {}}).status_code,
                         404)

    def test_a_locked_experiment_is_refused_with_the_locks_sentence(self):
        self._establish()
        self.experiment.locked_at = timezone.now()
        self.experiment.save(update_fields=["locked_at"])
        response = self._post({"experiment_id": self.experiment.id,
                               "annotators": {"t_view": {"enabled": True}}})
        self.assertEqual(response.status_code, 403)
        self.assertIn("locked", response.json()["error"].lower())
        self.assertEqual([], self.seen)

    def test_a_bad_selection_is_a_400(self):
        self._establish()
        response = self._post({"experiment_id": self.experiment.id,
                               "annotators": {"t_view": {"enabled": True, "depth": "x"}}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("View Thing", response.json()["error"])

    def test_nothing_ticked_is_a_400(self):
        self._establish()
        response = self._post({"experiment_id": self.experiment.id,
                               "annotators": {"t_view": {"enabled": False}}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tick", response.json()["error"])

    def test_no_reference_is_a_409(self):
        response = self._post({"experiment_id": self.experiment.id,
                               "annotators": {"t_view": {"enabled": True}}})
        self.assertEqual(response.status_code, 409)
        self.assertIn("reference", response.json()["error"])

    def test_an_import_in_progress_is_a_409(self):
        self._establish()
        import_lock.acquire(holder="somebody")
        self.addCleanup(import_lock.release)
        response = self._post({"experiment_id": self.experiment.id,
                               "annotators": {"t_view": {"enabled": True}}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual([], self.seen)

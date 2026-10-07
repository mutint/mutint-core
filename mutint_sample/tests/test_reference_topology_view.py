"""The Topology column on `/mutations/reference`, and the endpoint that writes it.

`mutint_import/tests/test_reference_topology.py` owns the rule and the two-places
invariant. What is asserted here is the contract around it: who may write, what the page
renders, and that a write reaches the stored file.
"""

import json

from django.contrib.auth.models import User
from django.utils import timezone

from mutint_common import store
from mutint_import import reference
from mutint_import.tests import breseq_fixture
from mutint_sample.tests.test_ncbi_view import _Fixture

URL = "/mutations/reference/topology"


class _Topology(_Fixture):
    SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A),
                 ("pKD46", breseq_fixture.SEQUENCE_B)]

    def _page(self):
        return self.client.get("/mutations/reference",
                               {"experiment_id": self.experiment.id})

    def _set(self, seq_ids, circular, experiment_id=None):
        return self.client.post(URL, data=json.dumps({
            "experiment_id": self.experiment.id if experiment_id is None else experiment_id,
            "seq_ids": seq_ids,
            "circular": circular,
        }), content_type="application/json")

    def _stored(self):
        self.reference.refresh_from_db()
        return {entry["id"]: entry.get("circular") for entry in self.reference.seq_ids}

    def _file_topologies(self):
        path = store.experiment_reference_path(self.experiment.id, store.REFERENCE_GFF3)
        with open(path, encoding="utf-8") as handle:
            return reference.gff3_topologies(handle.read())


class PageTestCase(_Topology):
    def test_the_column_renders_the_default_as_suggested(self):
        html = self._page().content.decode("utf-8")
        self.assertIn("<th>Topology</th>", html)
        self.assertEqual(html.count("The file this reference came from did not say"), 2)
        self.assertRegex(html, r'class="reference-topology">\s*linear\s*<')

    def test_a_set_topology_stops_being_marked_suggested(self):
        self._set(["test_ref"], "circular")
        html = self._page().content.decode("utf-8")
        self.assertEqual(html.count("The file this reference came from did not say"), 1)
        self.assertRegex(html, r'class="reference-topology">\s*circular\s*<')

    def test_a_writer_is_offered_the_control(self):
        html = self._page().content.decode("utf-8")
        self.assertIn("reference-topology-apply", html)
        self.assertIn('id="reference-topology-select"', html)
        self.assertIn('value="circular"', html)
        self.assertIn('value="linear"', html)
        self.assertIn(URL, html)

    def test_a_reader_sees_the_column_and_no_control(self):
        reader = User.objects.create(username="reader", is_active=True)
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.force_login(reader)
        html = self._page().content.decode("utf-8")
        self.assertIn("<th>Topology</th>", html)
        self.assertNotIn("reference-topology-apply", html)


class EndpointTestCase(_Topology):
    def test_a_writer_can_set_a_topology_and_the_file_follows(self):
        before = self.reference.gff3_sha256
        response = self._set(["test_ref"], "circular")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["changed"], 1)
        self.assertEqual(self._stored(), {"test_ref": True, "pKD46": None})
        self.assertEqual(self._file_topologies(), {"test_ref": True})
        self.assertNotEqual(self.reference.gff3_sha256, before)

    def test_one_request_sets_several(self):
        self._set(["test_ref", "pKD46"], "linear")
        self.assertEqual(self._stored(), {"test_ref": False, "pKD46": False})
        self.assertEqual(self._file_topologies(), {"test_ref": False, "pKD46": False})

    def test_a_null_clears_the_answer(self):
        self._set(["test_ref"], "circular")
        response = self._set(["test_ref"], None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._stored()["test_ref"], None)
        self.assertEqual(self._file_topologies(), {})

    def test_an_unknown_value_is_refused(self):
        response = self._set(["test_ref"], "round")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._stored()["test_ref"], None)

    def test_an_empty_selection_is_refused(self):
        self.assertEqual(self._set([], "circular").status_code, 400)

    def test_an_unknown_experiment_is_404(self):
        self.assertEqual(self._set(["test_ref"], "circular", experiment_id=99999).status_code,
                         404)

    def test_a_get_is_refused(self):
        self.assertEqual(self.client.get(URL).status_code, 405)


class PermissionTestCase(_Topology):
    def test_a_reader_cannot_set_a_topology(self):
        reader = User.objects.create(username="reader", is_active=True)
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.force_login(reader)
        self.assertEqual(self._set(["test_ref"], "circular").status_code, 403)
        self.assertEqual(self._stored()["test_ref"], None)

    def test_a_stranger_cannot_set_a_topology(self):
        stranger = User.objects.create(username="nobody", is_active=True)
        self.client.force_login(stranger)
        self.assertIn(self._set(["test_ref"], "circular").status_code, (403, 404))
        self.assertEqual(self._stored()["test_ref"], None)

    def test_a_locked_experiment_refuses(self):
        self.experiment.locked_at = timezone.now()
        self.experiment.locked_by = self.user
        self.experiment.save()
        self.assertEqual(self._set(["test_ref"], "circular").status_code, 403)
        self.assertEqual(self._stored()["test_ref"], None)

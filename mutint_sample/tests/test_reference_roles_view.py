"""The Role column on `/mutations/reference`, and the endpoint that writes it.

`mutint_import/tests/test_reference_roles.py` owns the rule itself. What is asserted here is
the contract around it: who may write, what the page renders, and that the control still
works in the state that removes every other form from the page.
"""

import json

from django.contrib.auth.models import User
from django.utils import timezone

from mutint_import.reference_roles import ROLE_CONTIG, ROLE_JUNCTION_ONLY, ROLE_REFERENCE
from mutint_import.tests import breseq_fixture
from mutint_sample.models import DatabaseSequenceLink
from mutint_sample.tests.test_ncbi_view import _Fixture

URL = "/mutations/reference/roles"


class _Roles(_Fixture):
    """Two contigs, one assembler-named and one not, so both guesses are on the page.

    `test_ref` keeps its name because `breseq_fixture`'s mutations are written against it;
    rename it and the fixture sample does not import at all.
    """

    SEQUENCES = [("test_ref", breseq_fixture.SEQUENCE_A),
                 ("NODE_1", breseq_fixture.SEQUENCE_B)]

    def _page(self):
        return self.client.get("/mutations/reference",
                               {"experiment_id": self.experiment.id})

    def _set(self, seq_ids, role, experiment_id=None):
        return self.client.post(URL, data=json.dumps({
            "experiment_id": self.experiment.id if experiment_id is None else experiment_id,
            "seq_ids": seq_ids,
            "role": role,
        }), content_type="application/json")

    def _roles(self):
        self.experiment.refresh_from_db()
        reference = type(self.reference).objects.get(experiment=self.experiment)
        return {entry["id"]: entry.get("role") for entry in reference.seq_ids}


class PageTestCase(_Roles):
    def test_the_column_renders_both_guesses(self):
        html = self._page().content.decode("utf-8")
        self.assertIn("<th>Role</th>", html)
        self.assertIn("Contig (-c)", html)
        self.assertIn("Reference (-r)", html)

    def test_a_guessed_role_says_so(self):
        """Nobody has set either of these, so both are suggestions and the page must not
        present them as answers."""
        html = self._page().content.decode("utf-8")
        self.assertEqual(html.count("reference-role-guessed"), 2)

    def test_a_set_role_stops_being_marked_suggested(self):
        self._set(["NODE_1"], ROLE_REFERENCE)
        html = self._page().content.decode("utf-8")
        self.assertEqual(html.count("reference-role-guessed"), 1)

    def test_a_writer_is_offered_the_control(self):
        html = self._page().content.decode("utf-8")
        self.assertIn("reference-role-apply", html)
        self.assertIn("Reset to suggested", html)
        self.assertIn(URL, html)

    def test_the_menu_offers_exactly_the_roles_the_endpoint_accepts(self):
        html = self._page().content.decode("utf-8")
        for role in (ROLE_REFERENCE, ROLE_CONTIG, ROLE_JUNCTION_ONLY):
            self.assertIn('value="%s"' % role, html)

    def test_a_reader_sees_the_roles_and_no_control(self):
        reader = User.objects.create(username="reader", is_active=True)
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.force_login(reader)

        html = self._page().content.decode("utf-8")
        self.assertIn("<th>Role</th>", html)
        self.assertIn("Contig (-c)", html)
        self.assertNotIn("reference-role-apply", html)

    def test_the_control_carries_its_own_csrf_token(self):
        """The per-row accession forms are gone once every contig is verified, and this
        control still has to work then -- so it must not borrow their token."""
        for entry in self.reference.seq_ids:
            DatabaseSequenceLink.objects.create(
                sha256=entry["sha256"], length=entry["length"],
                accession="NC_000913.3", status=DatabaseSequenceLink.VERIFIED,
                detail="matches", checked_at=timezone.now())

        html = self._page().content.decode("utf-8")
        # The per-row *forms* are what carried the token, and they are gone. Their handler
        # script still renders and is not what this is about.
        self.assertNotIn('class="form-inline ncbi-check"', html)
        self.assertIn("reference-role-apply", html)
        self.assertIn("csrfmiddlewaretoken", html)


class EndpointTestCase(_Roles):
    def test_a_writer_can_set_a_role(self):
        response = self._set(["NODE_1"], ROLE_JUNCTION_ONLY)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["changed"], 1)
        self.assertEqual(self._roles()["NODE_1"], ROLE_JUNCTION_ONLY)

    def test_it_reports_the_grouping_back(self):
        response = self._set(["NODE_1"], ROLE_JUNCTION_ONLY)
        self.assertEqual(response.json()["grouping"], "test_ref (-r), NODE_1 (-s)")

    def test_one_request_sets_several(self):
        self._set(["test_ref", "NODE_1"], ROLE_CONTIG)
        self.assertEqual(self._roles(), {"test_ref": ROLE_CONTIG, "NODE_1": ROLE_CONTIG})

    def test_a_null_role_clears_the_override(self):
        self._set(["NODE_1"], ROLE_REFERENCE)
        self.assertEqual(self._roles()["NODE_1"], ROLE_REFERENCE)
        response = self._set(["NODE_1"], None)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._roles()["NODE_1"])

    def test_an_unknown_role_is_refused(self):
        response = self._set(["NODE_1"], "junction")
        self.assertEqual(response.status_code, 400)
        self.assertIn("junction", response.json()["error"])
        self.assertIsNone(self._roles()["NODE_1"])

    def test_an_empty_selection_is_refused(self):
        self.assertEqual(self._set([], ROLE_CONTIG).status_code, 400)

    def test_a_body_that_is_not_json_is_refused(self):
        response = self.client.post(URL, data="not json",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_experiment_is_404(self):
        self.assertEqual(self._set(["NODE_1"], ROLE_CONTIG, experiment_id=99999).status_code,
                         404)

    def test_a_get_is_refused(self):
        self.assertEqual(self.client.get(URL).status_code, 405)


class PermissionTestCase(_Roles):
    def test_a_stranger_cannot_set_a_role(self):
        stranger = User.objects.create(username="nobody", is_active=True)
        self.client.force_login(stranger)
        self.assertIn(self._set(["NODE_1"], ROLE_CONTIG).status_code, (403, 404))
        self.assertIsNone(self._roles()["NODE_1"])

    def test_a_reader_cannot_set_a_role(self):
        """A role changes the command line of every future breseq run in the experiment,
        which is a shared write however small it looks."""
        reader = User.objects.create(username="reader", is_active=True)
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.force_login(reader)

        self.assertEqual(self._set(["NODE_1"], ROLE_CONTIG).status_code, 403)
        self.assertIsNone(self._roles()["NODE_1"])

    def test_an_anonymous_visitor_cannot_set_a_role(self):
        self.experiment.project.is_public = True
        self.experiment.project.save(update_fields=["is_public"])
        self.client.logout()

        self.assertEqual(self._set(["NODE_1"], ROLE_CONTIG).status_code, 403)
        self.assertIsNone(self._roles()["NODE_1"])

    def test_a_locked_experiment_refuses(self):
        """The case `can_edit_project` would miss: a predicate handed the project cannot
        see a flag that lives on the experiment."""
        self.experiment.locked_at = timezone.now()
        self.experiment.locked_by = self.user
        self.experiment.save()

        self.assertEqual(self._set(["NODE_1"], ROLE_CONTIG).status_code, 403)
        self.assertIsNone(self._roles()["NODE_1"])

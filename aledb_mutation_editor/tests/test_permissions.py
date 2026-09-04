"""Who may edit an experiment's mutations.

The four-actor matrix `aledb_seq.tests.test_table_actions` established, applied to all three
write endpoints. Every refusal is followed by an assertion that the write did not happen: a
status code alone would still pass if the endpoint refused *and* wrote, which is exactly the
failure the tag endpoints once had.

Deleting a mutation is curation, so it is gated on `can_add_experiment_filter` -- the same
predicate tagging uses, which resolves to write access on the project. Not `can_delete_project`,
which is admin: removing a false call is what a collaborator is for, and it is recoverable from
the history, which soft-deleting a whole project is only via a retention window.
"""

import json

from django.contrib.auth.models import User

from aledb_mutation_editor.models import MutationChangeSet
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import ObservedMutation

DELETE = "/mutation-editor/delete/apply"
COPY = "/mutation-editor/copy/apply"
ADD = "/mutation-editor/add/apply"
RESTORE = "/mutation-editor/restore"

PAGES = ("/mutation-editor/", "/mutation-editor/add", "/mutation-editor/copy",
         "/mutation-editor/history")


class WriteEndpointPermissionTestCase(EditorTestCase):

    def _delete_one(self):
        observed = ObservedMutation.objects.get(sample=self.sample_a,
                                                mutation=self.mut_2)
        return self.client.post(DELETE, {
            "experiment_id": self.experiment.id,
            "reseq_id": self.sample_a.id,
            "observed_ids": json.dumps([observed.id])})

    def _copy_one(self):
        return self.client.post(COPY, {
            "experiment_id": self.experiment.id,
            "source_reseq_id": self.sample_a.id,
            "mutation_ids": json.dumps([self.mut_2.id]),
            "target_reseq_ids": json.dumps([self.sample_b.id])})

    def _add_one(self):
        return self.client.post(ADD, {
            "experiment_id": self.experiment.id,
            "mutation_type": "SNP",
            "seq_id": "NC_000913",
            "position": 4242,
            "new_seq": "T",
            "target_reseq_ids": json.dumps([self.sample_a.id])})

    def _restore(self):
        return self.client.post(RESTORE, {
            "experiment_id": self.experiment.id,
            "change_set_id": "",
            "reseq_ids": "[]"})

    def _every_endpoint(self):
        return (("delete", self._delete_one), ("copy", self._copy_one),
                ("add", self._add_one), ("restore", self._restore))

    def _assert_nothing_was_written(self):
        self.assertEqual(4, self.observation_count(),
                         "the fixture's four observations are untouched")
        self.assertEqual(0, MutationChangeSet.objects.count())

    # --- who may -------------------------------------------------------------------------

    # One assertion each rather than three in a row: deleting mut_2 from sample A is exactly
    # what makes the copy's source lookup miss, so chaining them tested the fixture rather
    # than the permission.
    def test_the_owner_may_delete(self):
        self.assertEqual(200, self._delete_one().status_code)

    def test_the_owner_may_copy(self):
        self.assertEqual(200, self._copy_one().status_code)

    def test_the_owner_may_add(self):
        self.assertEqual(200, self._add_one().status_code)

    def test_the_owner_may_restore(self):
        self._delete_one()
        self.assertEqual(200, self._restore().status_code)

    def test_a_superuser_may_edit_anything(self):
        admin = User.objects.create(username="admin", email="a@e.com",
                                    is_active=True, is_superuser=True)
        self.client.force_login(admin)
        self.assertEqual(200, self._delete_one().status_code)

    def test_a_writer_may_delete(self):
        """**write**, not admin. Curating an experiment's mutations is what the write role is
        for -- `can_add_experiment_filter` delegates to `can_edit_experiment`, which is
        `has_project_role(..., ROLE_WRITE)`.

        Every other case here was covered and this one was not: owner, superuser, anonymous,
        stranger, staff-without-a-grant and reader all had tests, so "the lowest role that is
        allowed to edit actually can" was the one thing nobody had asserted.
        """
        self.client.force_login(self._writer())

        self.assertEqual(200, self._delete_one().status_code)
        self.assertEqual(3, self.observation_count(), "the observation is gone")

    def test_a_writer_may_copy(self):
        self.client.force_login(self._writer())
        self.assertEqual(200, self._copy_one().status_code)

    def test_a_writer_may_add(self):
        self.client.force_login(self._writer())
        self.assertEqual(200, self._add_one().status_code)

    def test_a_writer_may_change_a_mutation(self):
        self.client.force_login(self._writer())

        response = self.client.post("/mutation-editor/edit/apply", {
            "experiment_id": self.experiment.id,
            "mutation_id": self.mut_1.id,
            "mutation_type": "SNP",
            "seq_id": "NC_000913",
            "position": 150,
            "new_seq": "T"})

        self.assertEqual(200, response.status_code, response.content)

    def test_a_writer_may_open_the_editor_with_its_controls(self):
        """The endpoints answering 200 is not enough on its own: a page that hides the
        buttons would leave a writer unable to reach them."""
        self.client.force_login(self._writer())

        html = self.client.get("/mutation-editor/delete", {
            "ale_experiment_id": self.experiment.id,
            "reseq_id": "all"}).content.decode("utf-8")

        self.assertIn('id="me-apply"', html)

    def _writer(self):
        from aledb_experiment.permissions import grant_project_access
        from aledb_experiment.roles import ROLE_WRITE

        writer = User.objects.create(username="justawriter", email="w@e.com", is_active=True)
        grant_project_access(self.experiment.project, writer, ROLE_WRITE,
                             granted_by=self.owner)
        return writer

    # --- who may not ---------------------------------------------------------------------

    def test_anonymous_may_not_edit(self):
        self.client.logout()
        for name, call in self._every_endpoint():
            with self.subTest(endpoint=name):
                self.assertEqual(403, call().status_code)
        self._assert_nothing_was_written()

    def test_a_stranger_may_not_edit(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        for name, call in self._every_endpoint():
            with self.subTest(endpoint=name):
                self.assertEqual(403, call().status_code)
        self._assert_nothing_was_written()

    def test_staff_without_a_grant_may_not_edit(self):
        """Being staff is not access.

        `load_projects` marks every imported user staff, so a blanket staff clause here would
        make every project's mutations editable by nearly everyone.
        """
        staff = User.objects.create(username="staff", email="st@e.com",
                                    is_active=True, is_staff=True)
        self.client.force_login(staff)
        for name, call in self._every_endpoint():
            with self.subTest(endpoint=name):
                self.assertEqual(403, call().status_code)
        self._assert_nothing_was_written()

    # --- shape ---------------------------------------------------------------------------

    def test_the_endpoints_refuse_a_GET(self):
        for url in (DELETE, COPY, ADD, RESTORE):
            with self.subTest(url=url):
                self.assertEqual(405, self.client.get(url).status_code)

    def test_an_unknown_experiment_is_a_404_not_a_500(self):
        response = self.client.post(DELETE, {"experiment_id": 999999,
                                             "observed_ids": "[1]"})
        self.assertEqual(404, response.status_code)

    def test_the_refusal_is_json_a_caller_can_read(self):
        """These are reached by aledbPost, which reads `error` off the body."""
        self.client.logout()
        response = self._delete_one()
        self.assertEqual("application/json", response["Content-Type"])
        self.assertIn("permission", response.json()["error"])


class PagePermissionTestCase(EditorTestCase):

    def test_the_pages_render_for_someone_who_may_edit(self):
        for url in PAGES:
            with self.subTest(url=url):
                self.assertEqual(200, self.client.get(
                    url, {"ale_experiment_id": self.experiment.id}).status_code)

    def test_a_stranger_cannot_see_the_experiment_at_all(self):
        """`get_ale_experiment` gates on can_view_project before this app is consulted."""
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        for url in PAGES:
            with self.subTest(url=url):
                response = self.client.get(
                    url, {"ale_experiment_id": self.experiment.id})
                self.assertEqual(403, response.status_code)

    def test_a_reader_is_shown_the_data_without_the_controls(self):
        """The control being absent is presentation; the endpoint refusing is the check."""
        from aledb_experiment.permissions import grant_project_access
        from aledb_experiment.roles import ROLE_READ

        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        grant_project_access(self.experiment.project, reader, ROLE_READ)
        self.client.force_login(reader)

        response = self.client.get("/mutation-editor/",
                                   {"ale_experiment_id": self.experiment.id})
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, 'id="me-apply"')
        self.assertContains(response, "needs write access")

"""Locking an experiment against every write, whoever is asking.

The lock is not a fifth role. It answers "is this dataset still open", which outranks "who
are you" -- so the assertions worth having are the ones about people who *do* have permission
and are refused anyway.

`EveryWritePathTestCase` is the one that matters. It sweeps the endpoints rather than testing
the predicate, because the predicate was never the risk: the risk is a write path that does
not ask it, and a new one added later is exactly what a per-endpoint sweep catches and a unit
test of `can_edit_experiment` does not.
"""

import json

from django.contrib.auth.models import User

from mutint_experiment.models import Experiment, Project
from mutint_experiment.permissions import (
    ExperimentLocked, can_edit_experiment, can_lock_experiment, grant_project_access,
)
from mutint_experiment.roles import ROLE_ADMIN, ROLE_WRITE
from mutint_curate.tests.base import EditorTestCase
from mutint_sample.models import Mutation, MutationCall


class LockTestCase(EditorTestCase):
    """`EditorTestCase` gives an experiment with two samples and three mutations."""

    def lock(self):
        return self.experiment.lock(self.owner)

    def url(self, path):
        return path

    def post_lock(self, **data):
        return self.client.post(
            "/experiment/%d/lock/" % self.experiment.id, data)


class ModelTestCase(LockTestCase):

    def test_a_new_experiment_is_unlocked(self):
        self.assertFalse(self.experiment.is_locked)
        self.assertIsNone(self.experiment.locked_at)

    def test_locking_records_who_and_when(self):
        self.lock()

        self.experiment.refresh_from_db()
        self.assertTrue(self.experiment.is_locked)
        self.assertIsNotNone(self.experiment.locked_at)
        self.assertEqual(self.owner, self.experiment.locked_by)

    def test_unlocking_clears_both(self):
        self.lock()
        self.experiment.unlock()

        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.is_locked)
        self.assertIsNone(self.experiment.locked_at)
        self.assertIsNone(self.experiment.locked_by)

    def test_the_message_names_the_experiment_and_the_way_out(self):
        self.lock()
        message = self.experiment.lock_message()

        self.assertIn(self.experiment.name, message)
        self.assertIn("cannot be changed", message)
        self.assertIn("unlock", message)

    def test_the_shell_context_carries_it(self):
        """So a padlock renders on every experiment-scoped page, not just the Overview."""
        self.assertFalse(self.experiment.experiment_context()["experiment_locked"])
        self.lock()
        self.assertTrue(self.experiment.experiment_context()["experiment_locked"])


class PredicateTestCase(LockTestCase):

    def test_write_access_is_not_enough_once_locked(self):
        self.assertTrue(can_edit_experiment(self.owner, self.experiment))
        self.lock()
        self.assertFalse(can_edit_experiment(self.owner, self.experiment))

    def test_a_superuser_is_refused_too(self):
        admin = User.objects.create(username="super", email="s@e.com",
                                    is_active=True, is_superuser=True)
        self.lock()
        self.assertFalse(can_edit_experiment(admin, self.experiment))

    def test_locking_is_admin_level(self):
        writer = User.objects.create(username="writer", email="w@e.com", is_active=True)
        grant_project_access(self.experiment.project, writer, ROLE_WRITE)

        self.assertFalse(can_lock_experiment(writer, self.experiment))
        self.assertTrue(can_lock_experiment(self.owner, self.experiment))

    def test_an_experiment_with_no_project_cannot_be_locked(self):
        """`effective_role` answers None for a null project before it reaches its superuser
        branch, so nobody holds admin on one. Self-consistent: it also cannot get locked."""
        admin = User.objects.create(username="super2", email="s2@e.com",
                                    is_active=True, is_superuser=True)
        self.experiment.project = None
        self.experiment.save(update_fields=["project"])

        self.assertFalse(can_lock_experiment(admin, self.experiment))


class LockEndpointTestCase(LockTestCase):

    def test_an_admin_can_lock_and_unlock(self):
        response = self.post_lock(locked="1")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["locked"])

        self.experiment.refresh_from_db()
        self.assertTrue(self.experiment.is_locked)

        self.assertFalse(self.post_lock().json()["locked"])
        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.is_locked)

    def test_the_response_says_who_and_when(self):
        body = self.post_lock(locked="1").json()
        self.assertEqual("owner", body["locked_by"])
        self.assertIsNotNone(body["locked_at"])

    def test_a_posted_reason_is_ignored_rather_than_stored(self):
        """The column is gone and the dialog is a plain confirm; an old client cannot 500."""
        self.assertEqual(200, self.post_lock(locked="1", reason="published").status_code)
        self.experiment.refresh_from_db()
        self.assertTrue(self.experiment.is_locked)
        self.assertNotIn("published", self.experiment.lock_message())

    def test_a_write_user_cannot_lock(self):
        writer = User.objects.create(username="writer", email="w@e.com", is_active=True)
        grant_project_access(self.experiment.project, writer, ROLE_WRITE)
        self.client.force_login(writer)

        self.assertEqual(403, self.post_lock(locked="1").status_code)
        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.is_locked)

    def test_a_write_user_cannot_unlock(self):
        """The whole point: the people who ordinarily edit cannot lift it themselves."""
        self.lock()
        writer = User.objects.create(username="writer", email="w@e.com", is_active=True)
        grant_project_access(self.experiment.project, writer, ROLE_WRITE)
        self.client.force_login(writer)

        self.assertEqual(403, self.post_lock().status_code)
        self.experiment.refresh_from_db()
        self.assertTrue(self.experiment.is_locked)

    def test_an_admin_who_is_not_the_owner_can_unlock(self):
        self.lock()
        other = User.objects.create(username="admin2", email="a2@e.com", is_active=True)
        grant_project_access(self.experiment.project, other, ROLE_ADMIN)
        self.client.force_login(other)

        self.assertEqual(200, self.post_lock().status_code)
        self.experiment.refresh_from_db()
        self.assertFalse(self.experiment.is_locked)

    def test_anonymous_cannot_lock(self):
        self.client.logout()
        self.assertEqual(403, self.post_lock(locked="1").status_code)

    def test_locking_twice_does_not_move_the_timestamp(self):
        first = self.post_lock(locked="1").json()["locked_at"]
        second = self.post_lock(locked="1").json()["locked_at"]
        self.assertEqual(first, second, "idempotent, as project_delete is")

    def test_it_refuses_a_GET(self):
        self.assertEqual(
            405, self.client.get("/experiment/%d/lock/" % self.experiment.id).status_code)


class EveryWritePathTestCase(LockTestCase):
    """A locked experiment refuses every write in the web UI.

    The sweep, not the predicate. A write path that forgets to ask is the failure this is for.
    """

    def setUp(self):
        super().setUp()
        self.call = MutationCall.objects.filter(
            sample=self.sample_a).first()
        self.lock()

    def writes(self):
        """(name, callable) for every experiment-scoped write the web offers."""
        experiment_id = self.experiment.id
        return (
            ("mutation delete", lambda: self.client.post("/curate/delete/apply", {
                "experiment_id": experiment_id,
                "call_ids": json.dumps([self.call.id])})),
            ("mutation add", lambda: self.client.post("/curate/add/apply", {
                "experiment_id": experiment_id, "mutation_type": "SNP",
                "seq_id": "NC_000913", "position": 999, "new_seq": "T",
                "target_sample_ids": json.dumps([self.sample_a.id])})),
            ("mutation copy", lambda: self.client.post("/curate/copy/apply", {
                "experiment_id": experiment_id, "source_sample_id": self.sample_a.id,
                "mutation_ids": json.dumps([self.mut_2.id]),
                "target_sample_ids": json.dumps([self.sample_b.id])})),
            ("mutation restore", lambda: self.client.post("/curate/restore", {
                "experiment_id": experiment_id, "edit_set_id": "", "sample_ids": "[]"})),
            ("sample update", lambda: self.client.post(
                "/sample/%d/update/" % self.sample_a.id, {"source_name": "x"})),
            ("bulk sample update", lambda: self.client.post(
                "/experiment/%d/samples/update/" % experiment_id, {"rows": "[]"})),
            ("experiment update", lambda: self.client.post(
                "/experiment/%d/update/" % experiment_id, {"name": "renamed"})),
            ("experiment delete", lambda: self.client.post(
                "/experiment/%d/delete/" % experiment_id, {})),
            ("storage clear", lambda: self.client.post(
                "/experiment/%d/storage/clear/" % experiment_id, {"kind": "alignments"})),
        )

    def test_every_write_endpoint_refuses(self):
        for name, call in self.writes():
            with self.subTest(endpoint=name):
                self.assertIn(call().status_code, (400, 403),
                              "%s did not refuse a locked experiment" % name)

    def test_nothing_was_written_by_any_of_them(self):
        before = self.call_count()
        for _, call in self.writes():
            call()

        self.assertEqual(before, self.call_count())
        self.experiment.refresh_from_db()
        self.assertEqual("E", self.experiment.name, "experiment_update did not land")
        self.assertIsNone(self.experiment.deleted_at, "experiment_delete did not land")

    def test_the_edit_pages_refuse_as_well(self):
        """Not only the endpoints: a form you can fill in and never save is a dead end."""
        for url in ("/experiment/%d/edit/" % self.experiment.id,
                    "/experiment/%d/samples/" % self.experiment.id,
                    "/sample/%d/edit/" % self.sample_a.id,
                    "/import/?experiment_id=%d" % self.experiment.id):
            with self.subTest(url=url):
                self.assertEqual(403, self.client.get(url).status_code)

    def test_the_engine_refuses_even_called_directly(self):
        """Defense in depth: `apply_edits` trusts no caller."""
        from mutint_curate import history
        from mutint_curate.models import KIND_DELETE

        with self.assertRaises(ExperimentLocked):
            history.apply_edits(self.experiment, self.owner, KIND_DELETE,
                                  removals=[self.call])

    def test_an_import_is_refused_at_the_registry(self):
        """The one funnel every import type passes through, including plugin-registered ones."""
        import tempfile

        from mutint_common.import_registry import run_import

        with self.assertRaises(ExperimentLocked):
            run_import(self.experiment, tempfile.mkdtemp(), self.owner,
                       import_type="genomediff")

    def test_the_refusal_says_why(self):
        response = self.client.post("/experiment/%d/delete/" % self.experiment.id, {})
        error = response.json()["error"]
        self.assertIn("locked", error)
        self.assertIn(self.experiment.name, error, "lock_message() is what is carried through")
        self.assertIn("unlock", error)


class StillAllowedTestCase(LockTestCase):
    """What a lock deliberately does not stop."""

    def test_reading_the_experiment_still_works(self):
        self.lock()
        response = self.client.get("/stats", {"experiment_id": self.experiment.id},
                                   follow=True)
        self.assertEqual(200, response.status_code)

    def test_the_overview_says_it_is_locked(self):
        self.lock()
        response = self.client.get("/stats", {"experiment_id": self.experiment.id},
                                   follow=True)

        self.assertContains(response, "This experiment is locked")
        self.assertContains(response, self.owner.get_username(),
                            msg_prefix="the banner names who locked it, there being no reason")

    def test_lock_sits_between_edit_samples_and_delete(self):
        """One row of actions, in reading order. Three `{% if %}`s, so it is easy to reorder
        by accident and nothing else would notice."""
        html = self.client.get(
            "/stats", {"experiment_id": self.experiment.id},
            follow=True).content.decode()

        samples = html.index("/samples/")
        lock = html.index('id="lock-experiment"')
        delete = html.index('id="delete-experiment"')
        self.assertLess(samples, lock)
        self.assertLess(lock, delete)

    def test_the_edit_controls_are_gone_and_unlock_is_offered(self):
        self.lock()
        html = self.client.get(
            "/stats", {"experiment_id": self.experiment.id},
            follow=True).content.decode()

        self.assertNotIn('id="delete-experiment"', html)
        self.assertIn('id="unlock-experiment"', html)

    def test_rebuilds_still_run(self):
        """Derived data is a function of the mutations, not a change to them. A locked
        experiment whose counts silently went stale would be worse, not safer."""
        from mutint_common.models import DerivedDataState
        from mutint_common.rebuild_registry import register_rebuilder, unregister_rebuilder
        from mutint_curate import history

        # Core has no experiment-scoped rebuilder of its own any more -- the last one was
        # `experiment_filter`, which defaulted a shared filter row that no longer exists, and
        # what remains is the dashboard's two site-scoped totals. So this registers one, which
        # is what an installed plugin would contribute.
        register_rebuilder("test.locked", lambda experiment_id: None)
        self.addCleanup(unregister_rebuilder, "test.locked")

        self.lock()
        history.rebuild_after_edit(self.experiment)

        self.assertTrue(DerivedDataState.objects.filter(
            name="test.locked", experiment=self.experiment).exists())

    def test_access_can_still_be_changed(self):
        """Locking is per experiment, access is per project; freezing one must not freeze
        the other, or an admin cannot fix who may read a locked dataset."""
        colleague = User.objects.create(username="colleague", email="c@e.com",
                                        is_active=True)
        self.lock()

        response = self.client.post(
            "/project/%d/access/grant/" % self.experiment.project_id,
            {"username": colleague.get_username(), "role": ROLE_ADMIN})

        self.assertEqual(200, response.status_code)

    def test_unlock_then_edit_then_relock(self):
        self.lock()
        self.post_lock()

        call = MutationCall.objects.filter(
            sample=self.sample_a).first()
        response = self.client.post("/curate/delete/apply", {
            "experiment_id": self.experiment.id,
            "call_ids": json.dumps([call.id])})
        self.assertEqual(200, response.status_code)

        self.assertTrue(self.post_lock(locked="1").json()["locked"])


class ProjectDeleteTestCase(LockTestCase):

    def test_a_project_holding_a_locked_experiment_cannot_be_deleted(self):
        """Otherwise the lock is sidestepped by the most obvious adjacent button."""
        self.lock()
        response = self.client.post(
            "/project/%d/delete/" % self.experiment.project_id, {})

        self.assertEqual(409, response.status_code)
        self.experiment.project.refresh_from_db()
        self.assertIsNone(self.experiment.project.deleted_at)

    def test_the_refusal_names_the_experiment(self):
        self.lock()
        response = self.client.post(
            "/project/%d/delete/" % self.experiment.project_id, {})
        self.assertIn(self.experiment.name, response.json()["error"])

    def test_an_unlocked_project_still_deletes(self):
        response = self.client.post(
            "/project/%d/delete/" % self.experiment.project_id, {})
        self.assertEqual(200, response.status_code)

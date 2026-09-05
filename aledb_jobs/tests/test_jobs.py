"""Attribution, visibility and cancellation.

The cancellation tests deliberately drive the **real** database backend rather than the
immediate one the rest of the suite runs under, for the reason `aledb_import/tests/test_tasks.py`
gives: under the immediate backend `.enqueue()` means "run it now", so there is no such thing
as a queued job to cancel and nothing here would be exercising the behavior it claims to.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_experiment.models import Project
from aledb_import import tasks as import_tasks
from aledb_jobs import jobs as jobs_api
from aledb_jobs import queue
from aledb_jobs.models import Job

DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


class AttributionTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="asker", email="a@e.com", is_active=True)

    def test_enqueueing_records_who_asked(self):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1,
                               user=self.user, label="coverage for s1",
                               component="aledb_import")

        self.assertEqual(job.user, self.user)
        self.assertEqual(job.label, "coverage for s1")
        self.assertEqual(job.task_path, "aledb_import.tasks.build_coverage")
        self.assertTrue(job.task_result_id)

    def test_an_anonymous_asker_is_recorded_as_nobody(self):
        from django.contrib.auth.models import AnonymousUser

        job = jobs_api.enqueue(import_tasks.build_coverage, 1, user=AnonymousUser())
        self.assertIsNone(job.user)

    def test_a_job_is_not_cancellable_unless_it_says_so(self):
        # The default is the honest one: a task that does not poll the flag cannot be
        # stopped, and a button that silently does nothing is worse than no button.
        self.assertFalse(jobs_api.enqueue(import_tasks.build_coverage, 1).cancellable)

    def test_the_immediate_backend_cannot_report_a_status(self):
        """Worth pinning, because it makes the page look broken under the test settings.

        `ImmediateBackend` declares `supports_get_result = False`, so `get_result` raises and
        every job reads as "no longer on the queue". That is right -- an immediate backend
        keeps no results -- and it is why the cancellation tests below run against the database
        backend instead. Nothing but a test suite ever sees this.
        """
        job = jobs_api.enqueue(import_tasks.build_coverage, 123456789)
        self.assertEqual(queue.STATUS_UNKNOWN,
                         queue.status_of(job.task_result_id, job.task_path))


class VisibilityTestCase(TestCase):
    def setUp(self):
        self.mine = User.objects.create(username="mine", email="m@e.com", is_active=True)
        self.theirs = User.objects.create(username="theirs", email="t@e.com", is_active=True)
        self.root = User.objects.create(username="root", email="r@e.com", is_active=True,
                                        is_superuser=True)

        jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.mine, label="mine")
        jobs_api.enqueue(import_tasks.build_coverage, 2, user=self.theirs, label="theirs")
        jobs_api.enqueue(import_tasks.build_coverage, 3, label="nobody's")

    def test_a_user_sees_only_their_own(self):
        self.assertEqual(["mine"], [j.label for j in jobs_api.for_user(self.mine)])

    def test_a_superuser_sees_everything(self):
        self.assertEqual(3, jobs_api.for_user(self.root).count())

    def test_the_page_lists_yours(self):
        self.client.force_login(self.mine)
        response = self.client.get("/jobs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mine")
        self.assertNotContains(response, "theirs")

    def test_anonymous_is_refused(self):
        response = self.client.get("/jobs/")
        self.assertEqual(response.status_code, 403)

    def test_the_json_refuses_anonymous_too(self):
        # The page and its poll are two doors to the same rows; a check on one is not a check.
        self.assertEqual(self.client.get("/jobs/list").status_code, 403)

    def test_the_unattributed_section_is_superusers_only(self):
        # Matched on the section's own prose rather than on the word "Unattributed", which
        # appears in the page's JavaScript either way -- an assertion that passes because of a
        # function name is not an assertion about what renders.
        heading = "On the queue with no record of who asked"
        self.client.force_login(self.mine)
        self.assertNotContains(self.client.get("/jobs/"), heading)
        self.client.force_login(self.root)
        self.assertContains(self.client.get("/jobs/"), heading)


class MayCancelTestCase(TestCase):
    def setUp(self):
        self.mine = User.objects.create(username="mine", email="m@e.com", is_active=True)
        self.other = User.objects.create(username="other", email="o@e.com", is_active=True)
        self.root = User.objects.create(username="root", email="r@e.com", is_active=True,
                                        is_superuser=True)
        self.job = jobs_api.enqueue(import_tasks.build_coverage, 1,
                                    user=self.mine, cancellable=True)

    def test_the_asker_may(self):
        self.assertTrue(jobs_api.may_cancel(self.mine, self.job))

    def test_a_superuser_may(self):
        self.assertTrue(jobs_api.may_cancel(self.root, self.job))

    def test_nobody_else_may(self):
        # Deliberately not tied to the experiment's roles: a job is somebody's request for the
        # machine to do something, and an admin who did not ask for it has no more standing to
        # stop it than to stop a colleague's export.
        self.assertFalse(jobs_api.may_cancel(self.other, self.job))

    def test_someone_elses_job_is_a_404_not_a_403(self):
        # So the endpoint cannot be used to discover which job ids exist.
        self.client.force_login(self.other)
        response = self.client.post("/jobs/%d/cancel" % self.job.pk)
        self.assertEqual(response.status_code, 404)

    def test_a_job_that_cannot_be_stopped_says_so(self):
        plain = jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.mine)
        self.client.force_login(self.mine)
        response = self.client.post("/jobs/%d/cancel" % plain.pk)
        self.assertEqual(response.status_code, 409)
        self.assertIn("cannot be stopped", response.json()["error"])
        self.assertFalse(Job.objects.get(pk=plain.pk).cancel_requested)

    def test_cancel_must_be_a_post(self):
        self.client.force_login(self.mine)
        self.assertEqual(self.client.get("/jobs/%d/cancel" % self.job.pk).status_code, 405)


@override_settings(TASKS=DATABASE_BACKEND)
class CancellationTestCase(TestCase):
    """Against the backend a deployment actually runs, where a queued job exists."""

    def setUp(self):
        from django_tasks_db.models import DBTaskResult
        self.DBTaskResult = DBTaskResult
        self.user = User.objects.create(username="asker", email="a@e.com", is_active=True)
        self.client.force_login(self.user)

    def _worker(self):
        from django_tasks_db.management.commands.db_worker import Worker
        return Worker(queue_names=["default"], interval=0, batch=True,
                      backend_name="default", startup_delay=False, max_tasks=1,
                      worker_id="test", excluded_queue_names=[])

    def test_enqueueing_stores_the_task_and_runs_nothing(self):
        job = jobs_api.enqueue(import_tasks.build_coverage, 123456789,
                               user=self.user, cancellable=True)
        row = self.DBTaskResult.objects.get()
        self.assertEqual("READY", row.status)
        self.assertEqual(str(row.id), job.task_result_id)

    def test_cancelling_leaves_the_queue_row_alone(self):
        """The load-bearing assertion of the whole design.

        The tempting shortcut is to delete the queue row so the job never runs. Deleting one
        the worker has already claimed kills the worker: Django 6.1 raises `NotUpdated` on a
        zero-row `save(update_fields=...)`, `set_failed` raises again from inside the except
        block, and that escapes the run loop and takes the process down with every other job
        it would have run. So the flag is the whole mechanism and the queue is untouched.
        """
        job = jobs_api.enqueue(import_tasks.build_coverage, 123456789,
                               user=self.user, cancellable=True)

        self.client.post("/jobs/%d/cancel" % job.pk)

        self.assertTrue(Job.objects.get(pk=job.pk).cancel_requested)
        row = self.DBTaskResult.objects.get()          # still there
        self.assertEqual("READY", row.status)          # and untouched

    def test_the_worker_survives_running_a_cancelled_job(self):
        """A job cancelled before a worker reached it is still picked up, and that is fine.

        It is the honest cost of never touching the queue: the task starts, sees the flag and
        stops. What must not happen is the worker dying, which is what deleting the row would
        have caused.
        """
        job = jobs_api.enqueue(import_tasks.build_coverage, 123456789,
                               user=self.user, cancellable=True)
        jobs_api.request_cancel(job, by=self.user)

        row = self.DBTaskResult.objects.get()
        self._worker().run_task(row)

        row.refresh_from_db()
        self.assertIn(row.status, ("SUCCESSFUL", "FAILED"))

    def test_is_cancelled_is_what_a_task_polls(self):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1,
                               user=self.user, cancellable=True)

        self.assertFalse(jobs_api.is_cancelled(job.task_result_id))
        jobs_api.request_cancel(job, by=self.user)
        self.assertTrue(jobs_api.is_cancelled(job.task_result_id))

    def test_a_task_with_no_job_row_is_never_cancelled(self):
        # Coverage enqueued the plain way. `is_cancelled` must answer False rather than
        # raising, or every unattributed task would fail the moment it polled.
        result = import_tasks.build_coverage.enqueue(1)
        self.assertFalse(jobs_api.is_cancelled(str(result.id)))
        self.assertFalse(jobs_api.is_cancelled(""))
        self.assertFalse(jobs_api.is_cancelled(None))

    def test_cancelling_twice_is_idempotent(self):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1,
                               user=self.user, cancellable=True)
        first = jobs_api.request_cancel(job, by=self.user).cancel_requested_at
        again = jobs_api.request_cancel(Job.objects.get(pk=job.pk), by=self.user)
        self.assertEqual(first, again.cancel_requested_at)

    def test_check_cancelled_raises(self):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1,
                               user=self.user, cancellable=True)
        jobs_api.request_cancel(job, by=self.user)
        with self.assertRaises(jobs_api.JobCancelled):
            jobs_api.check_cancelled(job.task_result_id)

    def test_unattributed_lists_what_has_no_job(self):
        jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.user, label="mine")
        import_tasks.build_coverage.enqueue(2)

        known = list(Job.objects.values_list("task_result_id", flat=True))
        rows = queue.unattributed(known)

        self.assertEqual(1, len(rows))
        self.assertEqual("build_coverage", rows[0]["label"])


class UnattributedBackendTestCase(TestCase):
    def test_another_backend_lists_nothing_rather_than_raising(self):
        # "What else is on the queue" is not expressible in the standard API, so this is the
        # one function that names a backend. On any other it must degrade, not fail.
        with override_settings(TASKS={"default": {"BACKEND": "somewhere.else.Backend"}}):
            self.assertEqual([], queue.unattributed([]))


class StatusTestCase(TestCase):
    def test_an_unknown_result_is_not_an_error(self):
        # A pruned result, a renamed task, an unreachable backend: all the same answer, and
        # none of them worth breaking a page that is mostly other rows.
        self.assertEqual(queue.STATUS_UNKNOWN, queue.status_of("", ""))
        self.assertEqual(queue.STATUS_UNKNOWN, queue.status_of("nope", "no.such.module.task"))
        self.assertEqual(queue.STATUS_UNKNOWN,
                         queue.status_of("00000000-0000-0000-0000-000000000000",
                                         "aledb_import.tasks.build_coverage"))

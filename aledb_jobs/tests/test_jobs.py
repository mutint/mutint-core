"""Attribution, visibility and cancellation.

The cancellation tests deliberately drive the **real** database backend rather than the
immediate one the rest of the suite runs under, for the reason `aledb_import/tests/test_tasks.py`
gives: under the immediate backend `.enqueue()` means "run it now", so there is no such thing
as a queued job to cancel and nothing here would be exercising the behavior it claims to.
"""

import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

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


@override_settings(TASKS=DATABASE_BACKEND)
class ReapStrandedTestCase(TestCase):
    """Clearing queue rows nothing will ever finish.

    **Nothing else clears them.** `django_tasks_db`'s own `prune_db_task_results` filters
    `DBTaskResult.objects.finished()` -- SUCCESSFUL or FAILED -- so a row no worker ever
    claimed is outside its remit by construction and lives for ever. Four accumulated in this
    suite's two dev databases before anybody looked.
    """

    def setUp(self):
        from django_tasks_db.models import DBTaskResult
        self.DBTaskResult = DBTaskResult
        self.user = User.objects.create(username="reaper", email="r@e.com", is_active=True)

    def _age(self, row, days):
        old = timezone.now() - timezone.timedelta(days=days)
        self.DBTaskResult.objects.filter(pk=row.pk).update(enqueued_at=old, started_at=old)
        row.refresh_from_db()
        return row

    def _queued(self, days=None):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.user)
        row = self.DBTaskResult.objects.get(id=job.task_result_id)
        if days is not None:
            self._age(row, days)
        return job, row

    def test_an_old_ready_row_goes(self):
        job, _row = self._queued(days=30)

        rows, orphans = jobs_api.reap_stranded()

        self.assertEqual((1, 1), (rows, orphans))
        self.assertEqual(0, self.DBTaskResult.objects.count())
        self.assertFalse(Job.objects.filter(pk=job.pk).exists())

    def test_a_recent_one_stays(self):
        """A row from this morning is *waiting for a worker*, which is a different thing
        entirely; age is the only way to tell the two apart."""
        self._queued()

        self.assertEqual((0, 0), jobs_api.reap_stranded())
        self.assertEqual(1, self.DBTaskResult.objects.count())

    def test_an_old_running_row_goes_too(self):
        """These have a real source: `start`'s shutdown kills the worker's group after a few
        seconds rather than waiting out a twelve-hour breseq run, and there is no reaper for a
        claimed row, so a task in flight at Ctrl-C says RUNNING for ever."""
        _job, row = self._queued(days=30)
        self.DBTaskResult.objects.filter(pk=row.pk).update(status="RUNNING")

        self.assertEqual(1, jobs_api.reap_stranded()[0])
        self.assertEqual(0, self.DBTaskResult.objects.count())

    def test_finished_rows_are_left_to_the_library_s_own_pruner(self):
        """Two reapers with overlapping remits is how a row gets deleted by whichever runs
        first and reported by neither."""
        _job, row = self._queued(days=30)
        self.DBTaskResult.objects.filter(pk=row.pk).update(status="SUCCESSFUL")

        self.assertEqual((0, 0), jobs_api.reap_stranded())
        self.assertEqual(1, self.DBTaskResult.objects.count())

    def test_a_job_whose_row_it_did_not_remove_is_left_alone(self):
        """A `Job` whose result the library's pruner already discarded is not litter -- it is
        the ordinary end of a finished job, which `STATUS_UNKNOWN` exists to render. Deleting
        it would throw away the installation's record of work it did, which is what every
        SET_NULL on that model is there to preserve."""
        job = Job.objects.create(task_result_id="no-such-row", task_path="x.y",
                                 user=self.user, label="old")

        jobs_api.reap_stranded()

        self.assertTrue(Job.objects.filter(pk=job.pk).exists())

    def test_a_dry_run_removes_nothing_and_still_counts(self):
        self._queued(days=30)

        self.assertEqual((1, 1), jobs_api.reap_stranded(dry_run=True))
        self.assertEqual(1, self.DBTaskResult.objects.count())

    def test_now_is_injectable(self):
        """The shape `reap_expired_sessions` established, so a test need not wait a
        fortnight."""
        self._queued()

        rows, _orphans = jobs_api.reap_stranded(
            now=timezone.now() + timezone.timedelta(days=30))

        self.assertEqual(1, rows)

    def test_it_skips_rows_a_worker_is_holding(self):
        """**The assertion that matters.** The worker claims inside `SELECT ... FOR UPDATE SKIP
        LOCKED`, so a plain `.filter(status=READY).delete()` can read a row as READY, block on
        the worker's lock, and delete one that has since become RUNNING -- after which
        `set_successful` raises `Model.NotUpdated` on its zero-row save, `set_failed` raises
        again from inside the except block, and the exception escapes the run loop and takes
        the worker process down with every other job it would have run.

        Asserted on the queryset rather than by racing two connections: `skip_locked` is
        visible in the SQL, and a race test that passes once proves nothing.
        """
        self._queued(days=30)
        captured = {}
        original = self.DBTaskResult.objects.filter

        def remember(*args, **kwargs):
            queryset = original(*args, **kwargs)
            captured.setdefault("first", queryset)
            return queryset

        with mock.patch.object(self.DBTaskResult.objects, "filter", remember):
            jobs_api.reap_stranded()

        self.assertIn("FOR UPDATE SKIP LOCKED",
                      str(captured["first"].select_for_update(skip_locked=True).query))
        self.assertEqual(0, self.DBTaskResult.objects.count())


@override_settings(TASKS=DATABASE_BACKEND)
class StalledQueueTestCase(TestCase):
    """The closest thing to "is a worker running" that can honestly be asked.

    `django_tasks_db` keeps no worker registry and no heartbeat -- only `worker_ids`, written
    onto rows a worker has already claimed. So the page reports an observation and hedges the
    conclusion, because a worker halfway through a twelve-hour breseq run looks from here
    exactly like no worker at all.
    """

    def setUp(self):
        from django_tasks_db.models import DBTaskResult
        self.DBTaskResult = DBTaskResult
        self.user = User.objects.create(username="watcher", email="w@e.com", is_active=True)
        self.client.force_login(self.user)

    def _queued(self, seconds_ago=0):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.user)
        if seconds_ago:
            self.DBTaskResult.objects.filter(id=job.task_result_id).update(
                enqueued_at=timezone.now() - timezone.timedelta(seconds=seconds_ago))
        return job

    def test_an_empty_queue_says_nothing(self):
        self.assertIsNone(queue.oldest_ready())

    def test_it_answers_with_the_head_of_the_queue(self):
        self._queued()
        self.assertIsNotNone(queue.oldest_ready())

    def test_a_running_row_is_not_waiting(self):
        job = self._queued()
        self.DBTaskResult.objects.filter(id=job.task_result_id).update(status="RUNNING")

        self.assertIsNone(queue.oldest_ready())

    def test_a_row_on_another_queue_is_not_ours_to_worry_about(self):
        """Or a row nobody runs a worker for would accuse a perfectly healthy one for ever."""
        job = self._queued()
        self.DBTaskResult.objects.filter(id=job.task_result_id).update(queue_name="elsewhere")

        self.assertIsNone(queue.oldest_ready())

    def _stalled_on_page(self):
        """What the server decided, read out of the page's json_script.

        Asserted here rather than on the banner's wording, which appears in the page's own
        JavaScript whether or not it is shown -- so a text search matches every render and
        proves nothing. Found by writing it the obvious way first.
        """
        page = self.client.get("/jobs/").content.decode()
        marker = '<script id="jobs-stalled-data" type="application/json">'
        rest = page[page.index(marker) + len(marker):]
        return json.loads(rest[:rest.index("</script>")])

    def test_the_page_warns_only_once_it_has_waited(self):
        """Not five seconds: a page loaded a second after an import would accuse a worker that
        is about to claim the row, and an indicator that cries wolf is worse than none."""
        self._queued()
        self.assertIsNone(self._stalled_on_page())

        self._queued(seconds_ago=queue.STALLED_SECONDS + 60)
        self.assertIsNotNone(self._stalled_on_page())

    def test_the_poll_carries_it_too(self):
        """The page refreshes every few seconds, so a banner written only into the first render
        would go stale in the wrong direction -- still accusing a worker that started a minute
        ago."""
        self._queued(seconds_ago=queue.STALLED_SECONDS + 60)

        body = self.client.get("/jobs/list").json()

        self.assertIsNotNone(body["stalled_since"])


@override_settings(TASKS=DATABASE_BACKEND)
class UnattributedOrderTestCase(TestCase):
    """Unfinished rows first, which the code did not do and its own comment claimed it did.

    It was one `order_by("-enqueued_at")[:50]`, so on any installation with more than fifty
    queue rows a stranded READY row from last month was pushed off the list by this morning's
    successes -- and for work enqueued without a `Job`, this panel is the only place it is
    visible at all.
    """

    def setUp(self):
        from django_tasks_db.models import DBTaskResult
        self.DBTaskResult = DBTaskResult
        self.user = User.objects.create(username="super", email="s@e.com",
                                        is_active=True, is_superuser=True)

    def _row(self, status, days_ago):
        job = jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.user)
        when = timezone.now() - timezone.timedelta(days=days_ago)
        self.DBTaskResult.objects.filter(id=job.task_result_id).update(
            status=status, enqueued_at=when, finished_at=when)
        Job.objects.filter(pk=job.pk).delete()
        return job.task_result_id

    def test_an_old_ready_row_survives_a_flood_of_recent_successes(self):
        stranded = self._row("READY", days_ago=60)
        for _ in range(queue.UNATTRIBUTED_LIMIT + 5):
            self._row("SUCCESSFUL", days_ago=0)

        listed = queue.unattributed([])

        self.assertEqual(queue.UNATTRIBUTED_LIMIT, len(listed))
        self.assertEqual("READY", listed[0]["status"])
        self.assertEqual(1, len([r for r in listed if r["status"] == "READY"]))
        self.assertTrue(self.DBTaskResult.objects.filter(id=stranded).exists())


class CancellationFlagIsSafeToAskTestCase(TestCase):
    """`is_cancelled` cannot raise, and that is not defensive habit.

    A dev database predating this app has no `aledb_jobs_job` table, and `build_coverage` now
    asks this before it does anything -- so the coverage task failed with `UndefinedTable`,
    reported as a coverage failure whose message said nothing about coverage. Same posture as
    `queue.status_of` beside it and `rebuild_registry.ensure_fresh` on a read path: a question
    *about* a job must not be able to take down the job it is about.
    """

    def test_it_answers_not_cancelled_when_it_cannot_ask(self):
        with mock.patch.object(Job.objects, "filter",
                               side_effect=Exception("relation does not exist")):
            self.assertFalse(jobs_api.is_cancelled("some-id"))

    def test_the_direction_is_deliberate(self):
        """"Cannot tell" means not cancelled: the cost is a cancellation ignored, which the
        person can see and ask for again. The other way silently skips work nobody cancelled."""
        job = Job.objects.create(task_result_id="abc", task_path="x.y", label="j")
        jobs_api.request_cancel(job)

        self.assertTrue(jobs_api.is_cancelled("abc"))

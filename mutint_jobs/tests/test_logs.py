"""A job's log file: written, read back, compressed, and thrown away with the row."""

import gzip
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_jobs import logs
from mutint_jobs.models import Job


class LogTestCaseBase(TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create(username="asker", email="a@e.com", is_active=True)
        self.job = Job.objects.create(task_result_id="result-1", task_path="app.tasks.thing",
                                      user=self.user, label="A job")

    def write(self, text, task_result_id="result-1"):
        with logs.open_log(task_result_id) as log:
            logs.write(log, text)


class WritingTestCase(LogTestCaseBase):
    def test_a_line_goes_in_and_comes_back(self):
        self.write("hello")

        text, truncated = logs.read_tail("result-1")
        self.assertEqual("hello\n", text)
        self.assertFalse(truncated)

    def test_the_log_is_appended_to_rather_than_replaced(self):
        """A task may open its log more than once -- fastp, then breseq -- and a retry must not
        erase what the first attempt said."""
        self.write("first")
        self.write("second")

        text, _ = logs.read_tail("result-1")
        self.assertEqual("first\nsecond\n", text)

    def test_it_is_readable_while_the_handle_is_still_open(self):
        """The whole point: a three-hour run has to be readable an hour in."""
        with logs.open_log("result-1") as log:
            logs.write(log, "started")
            text, _ = logs.read_tail("result-1")
            self.assertIn("started", text)

    def test_there_is_no_log_until_something_writes_one(self):
        self.assertFalse(logs.exists("result-1"))
        self.assertEqual(("", False), logs.read_tail("result-1"))

    def test_a_task_with_no_queue_id_writes_nowhere(self):
        """A task called directly, outside any queue, has no id to key a log by. Writing to
        nowhere beats raising: a log is a record of the work, not a prerequisite for it."""
        for missing in (None, ""):
            with logs.open_log(missing) as log:
                self.assertIsInstance(log, logs._NullLog)
                logs.write(log, "into the void")

        self.assertFalse(logs.exists("result-1"))

    def test_a_log_is_written_before_any_job_row_exists(self):
        """The reason the key is the queue's id and not `Job.pk`.

        Under an immediate backend the task runs *inside* `jobs.enqueue`, before the row it
        will get has been created. A pk-keyed log would go nowhere, silently.
        """
        Job.objects.all().delete()

        with logs.open_log("result-1") as log:
            logs.write(log, "written with no row in sight")

        Job.objects.create(task_result_id="result-1", task_path="app.tasks.thing",
                           user=self.user, label="Late row")
        text, _ = logs.read_tail("result-1")
        self.assertIn("written with no row in sight", text)


class TailTestCase(LogTestCaseBase):
    def test_a_long_log_is_cut_and_says_so(self):
        with logs.open_log("result-1") as log:
            for index in range(4000):
                logs.write(log, "line %05d %s" % (index, "x" * 80))

        text, truncated = logs.read_tail("result-1", tail_bytes=4096)
        self.assertTrue(truncated, "a log past the cap must report itself cut")
        self.assertLessEqual(len(text), 4096)
        self.assertIn("line 03999", text, "the tail is the end, not the beginning")
        self.assertNotIn("line 00000", text)

    def test_a_cut_log_does_not_start_mid_line(self):
        """A partial first line reads as corruption rather than as a cut."""
        with logs.open_log("result-1") as log:
            for index in range(500):
                logs.write(log, "%05d %s" % (index, "y" * 60))

        text, truncated = logs.read_tail("result-1", tail_bytes=1000)
        self.assertTrue(truncated)
        self.assertRegex(text.split("\n")[0], r"^\d{5} y+$")

    def test_the_whole_log_streams(self):
        self.write("alpha")
        self.write("omega")

        self.assertEqual("alpha\nomega\n",
                         b"".join(logs.stream("result-1")).decode("utf-8"))

    def test_streaming_a_job_with_no_log_yields_nothing(self):
        self.assertEqual([], list(logs.stream("result-1")))


class CompressionTestCase(LogTestCaseBase):
    def test_a_finished_log_is_gzipped_and_still_reads(self):
        self.write("something a tool said")

        logs.compress("result-1")

        self.assertTrue(os.path.isfile(logs.log_path("result-1", compressed=True)))
        self.assertFalse(os.path.isfile(logs.log_path("result-1")),
                         "the plain copy is removed once the gzip is complete")
        text, _ = logs.read_tail("result-1")
        self.assertEqual("something a tool said\n", text)
        self.assertEqual("something a tool said\n",
                         b"".join(logs.stream("result-1")).decode("utf-8"))

    def test_it_really_is_gzip(self):
        self.write("compress me")
        logs.compress("result-1")

        with gzip.open(logs.log_path("result-1", compressed=True), "rb") as handle:
            self.assertEqual(b"compress me\n", handle.read())

    def test_compressing_nothing_is_not_an_error(self):
        """A job that ran no tool never wrote a log, and `task_finished` fires anyway."""
        logs.compress("result-1")
        self.assertFalse(logs.exists("result-1"))

    def test_the_task_finished_signal_compresses_it(self):
        """No task has a line for this: one receiver covers every job. See receivers.py.

        The stand-in carries `status` and `task` as well as `id` because Django's own
        `log_task_finished` is on this signal too and reads all three.
        """
        from django.tasks.base import TaskResultStatus
        from django.tasks.signals import task_finished

        self.write("worker output")

        class _Task:
            module_path = "app.tasks.thing"

        class _Result:
            id = "result-1"
            status = TaskResultStatus.SUCCESSFUL
            task = _Task()

        task_finished.send(sender=self.__class__, task_result=_Result())

        self.assertTrue(os.path.isfile(logs.log_path("result-1", compressed=True)))

    def test_an_uncompressed_log_is_still_readable(self):
        """A worker killed outright never fires `task_finished`, so this is a real state."""
        self.write("killed mid-run")

        self.assertTrue(os.path.isfile(logs.log_path("result-1")))
        text, _ = logs.read_tail("result-1")
        self.assertEqual("killed mid-run\n", text)


class LifecycleTestCase(LogTestCaseBase):
    def test_deleting_the_job_removes_its_log(self):
        """`store.component_dir` is reaped by nothing, so the row owns its directory."""
        self.write("goes with the row")
        directory = logs.log_dir("result-1")
        self.assertTrue(os.path.isdir(directory))

        self.job.delete()

        self.assertFalse(os.path.exists(directory))

    def test_discarding_a_job_with_no_log_is_not_an_error(self):
        logs.discard("result-1")

    def test_log_urls_names_only_jobs_with_a_log(self):
        """What a component holds is the queue's id; the mapping to a page is core's."""
        from mutint_jobs import jobs as jobs_api

        Job.objects.create(task_result_id="result-2", task_path="app.tasks.thing",
                           user=self.user, label="Silent job")
        self.write("noisy")

        urls = jobs_api.log_urls(["result-1", "result-2", "result-missing"])

        self.assertEqual(["result-1"], list(urls))
        self.assertEqual("/jobs/%d/log" % self.job.pk, urls["result-1"])


class LogPageTestCase(LogTestCaseBase):
    """`/jobs/<pk>/log` -- who may read it, and what it says about what it is showing."""

    def setUp(self):
        super().setUp()
        self.user.set_password("a-long-enough-password")
        self.user.save()
        self.other = User.objects.create_user(
            username="somebody", email="s@e.com", password="a-long-enough-password")
        self.root = User.objects.create_superuser(
            username="root", email="r@e.com", password="a-long-enough-password")
        self.url = "/jobs/%d/log" % self.job.pk

    def get(self, user, url=None):
        self.client.force_login(user)
        return self.client.get(url or self.url)

    def test_the_person_who_asked_can_read_it(self):
        self.write("what the tool said")

        response = self.get(self.user)

        self.assertEqual(200, response.status_code)
        self.assertIn("what the tool said", response.content.decode())

    def test_a_superuser_can_read_anybody_s(self):
        self.write("what the tool said")

        self.assertIn("what the tool said", self.get(self.root).content.decode())

    def test_somebody_else_gets_a_404_rather_than_a_403(self):
        """A 403 tells the caller the id exists, and job ids are sequential. Same posture as
        `job_cancel` and the breseq report."""
        self.write("private")

        self.assertEqual(404, self.get(self.other).status_code)
        self.assertEqual(404, self.get(self.other, self.url + "/download").status_code)

    def test_a_job_that_printed_nothing_says_so(self):
        response = self.get(self.user)

        self.assertEqual(200, response.status_code)
        self.assertIn("has not printed anything", response.content.decode())

    def test_a_cut_log_says_it_is_cut(self):
        """The Top button would otherwise jump to a beginning that is not the log's."""
        with logs.open_log("result-1") as log:
            logs.write(log, "x" * (logs.TAIL_BYTES + 5000))

        body = self.get(self.user).content.decode()

        self.assertIn("Showing the last", body)
        self.assertIn("Download the log", body)

    def test_a_short_log_says_nothing_about_cutting(self):
        """`hidden`, not absent, and that is the change: the notice now lives in the page from
        the start so the poll can reveal it when a log crosses the cut while somebody is
        watching. What matters to a reader -- that they are not told about a cut that has not
        happened -- is the same either way."""
        self.write("brief")

        body = self.get(self.user).content.decode()
        self.assertIn('id="job-log-cut" hidden', body)

    def test_the_download_is_the_whole_log(self):
        with logs.open_log("result-1") as log:
            logs.write(log, "first line")
            logs.write(log, "y" * (logs.TAIL_BYTES + 100))
            logs.write(log, "last line")

        response = self.get(self.user, self.url + "/download")
        body = b"".join(response.streaming_content).decode()

        self.assertEqual(200, response.status_code)
        self.assertIn("first line", body, "the download is not the tail")
        self.assertIn("last line", body)
        self.assertEqual("nosniff", response["X-Content-Type-Options"])
        self.assertIn("attachment", response["Content-Disposition"])

    def test_downloading_a_job_with_no_log_is_a_404(self):
        self.assertEqual(404, self.get(self.user, self.url + "/download").status_code)

    def test_the_jobs_list_links_only_jobs_that_have_one(self):
        Job.objects.create(task_result_id="result-2", task_path="app.tasks.thing",
                           user=self.user, label="Silent job")
        self.write("noisy")

        self.client.force_login(self.user)
        rows = self.client.get("/jobs/list").json()["jobs"]

        by_label = {row["label"]: row["log"] for row in rows}
        self.assertEqual("/jobs/%d/log" % self.job.pk, by_label["A job"])
        self.assertEqual("", by_label["Silent job"])


class NoIdTestCase(TestCase):
    """Every reader has to mean "there is no log" when there is no id to look one up by.

    Not a hypothetical: a task called directly, as several of this suite's tests do, has no
    queue identity at all. Before these guards `store.component_dir` reached `int(None)` and
    the breseq task died with a TypeError, having already set its row to `running` -- a
    failure with no error message anywhere.
    """

    def test_every_reader_answers_for_an_id_that_is_not_there(self):
        for missing in (None, ""):
            self.assertIsNone(logs.log_dir(missing))
            self.assertIsNone(logs.log_path(missing))
            self.assertIsNone(logs.stored_path(missing))
            self.assertFalse(logs.exists(missing))
            self.assertEqual(("", False), logs.read_tail(missing))
            self.assertEqual([], list(logs.stream(missing)))

    def test_every_writer_does_too(self):
        logs.compress(None)
        logs.discard(None)

    def test_a_key_that_is_not_a_plain_token_is_refused(self):
        """`component_dir` admits a pk or a generated id and nothing with a separator in it."""
        self.assertIsNone(logs.log_dir("../../etc"))
        self.assertIsNone(logs.log_dir("a/b"))


class OrphanSweepTestCase(LogTestCaseBase):
    """Logs belonging to no job row, which nothing else can reach.

    The cost of keying by the queue's id: work enqueued with a bare `task.enqueue` has no row,
    so no `post_delete` will ever take its log. `./mutint reap_jobs` is the sweep.
    """

    def test_a_log_with_a_job_is_not_an_orphan(self):
        self.write("mine")

        self.assertEqual([], logs.orphans())

    def test_a_log_with_no_job_is(self):
        self.write("nobody's", task_result_id="result-nobody")

        self.assertEqual(["result-nobody"], logs.orphans())

    def test_reap_jobs_removes_it_and_leaves_the_others(self):
        from django.core.management import call_command
        from io import StringIO

        self.write("mine")
        self.write("nobody's", task_result_id="result-nobody")

        out = StringIO()
        call_command("reap_jobs", stdout=out)

        self.assertIn("1 job log(s) belonging to no job row", out.getvalue())
        self.assertFalse(logs.exists("result-nobody"))
        self.assertTrue(logs.exists("result-1"), "a log with a row was swept too")

    def test_a_dry_run_removes_nothing(self):
        from django.core.management import call_command
        from io import StringIO

        self.write("nobody's", task_result_id="result-nobody")

        call_command("reap_jobs", "--dry-run", stdout=StringIO())

        self.assertTrue(logs.exists("result-nobody"))


class StatusLabelTestCase(LogTestCaseBase):
    def test_the_log_page_names_a_status_the_way_the_jobs_page_does(self):
        """Under the suite's own settings every status reads `unknown`, because
        `ImmediateBackend.supports_get_result` is False. What must not happen is this page
        printing that token while `/jobs/` calls the same thing "No longer on the queue"."""
        from mutint_jobs import queue

        self.user.set_password("a-long-enough-password")
        self.user.save()
        self.write("something")
        self.client.force_login(self.user)

        body = self.client.get("/jobs/%d/log" % self.job.pk).content.decode()

        self.assertIn(queue.STATUS_LABELS[queue.STATUS_UNKNOWN], body)
        self.assertNotIn(">unknown<", body)

    def test_an_unrecognised_status_is_passed_through(self):
        from mutint_jobs import queue

        self.assertEqual("something-new", queue.label_for("something-new"))
        self.assertEqual("", queue.label_for(""))


class WritingAgainTestCase(LogTestCaseBase):
    """A job that writes after its log was compressed.

    Reachable through a retry, or a task called a second time against the same row. The new
    output goes to a fresh plain file beside the gzip, and the reader has to show *that* --
    preferring the gzip showed the previous run's log and hid the one in progress.
    """

    def test_the_newer_plain_log_wins_over_an_older_gzip(self):
        self.write("the first attempt")
        logs.compress("result-1")

        self.write("the second attempt")

        text, _ = logs.read_tail("result-1")
        self.assertIn("the second attempt", text)
        self.assertNotIn("the first attempt", text)

    def test_a_compressed_log_alone_still_reads(self):
        self.write("only attempt")
        logs.compress("result-1")

        text, _ = logs.read_tail("result-1")
        self.assertEqual("only attempt\n", text)


DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


class LogTailTestCase(LogTestCaseBase):
    """`/jobs/<pk>/log/tail` -- the same tail as the page, as JSON, for its poll."""

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(
            username="somebody", email="s@e.com", password="a-long-enough-password")
        self.root = User.objects.create_superuser(
            username="root", email="r@e.com", password="a-long-enough-password")
        self.url = "/jobs/%d/log" % self.job.pk
        self.tail_url = self.url + "/tail"

    def get(self, user, url=None):
        self.client.force_login(user)
        return self.client.get(url or self.url)

    def test_it_answers_what_the_page_rendered(self):
        self.write("what the tool said")

        body = self.get(self.user, self.tail_url).json()

        self.assertEqual("what the tool said\n", body["text"])
        self.assertFalse(body["truncated"])

    def test_a_job_that_printed_nothing_answers_empty_rather_than_failing(self):
        """The module's rule for every reader: a missing log is a state, not an error. The
        page polls from the moment it loads, which is before a queued job has written."""
        body = self.get(self.user, self.tail_url).json()

        self.assertEqual(200, self.get(self.user, self.tail_url).status_code)
        self.assertEqual("", body["text"])

    def test_a_cut_log_says_so_here_too(self):
        """The page hides that notice until it is true, so the poll is what reveals it when a
        log crosses the cut while somebody is watching."""
        self.write("x" * (logs.TAIL_BYTES + 100))

        self.assertTrue(self.get(self.user, self.tail_url).json()["truncated"])

    def test_somebody_else_gets_a_404_rather_than_a_403(self):
        """Same posture as the page and the download: a 403 tells the caller the id exists,
        and job ids are sequential."""
        self.write("private")

        self.assertEqual(404, self.get(self.other, self.tail_url).status_code)

    def test_a_superuser_can_read_anybody_s(self):
        self.write("what the tool said")

        self.assertEqual("what the tool said\n",
                         self.get(self.root, self.tail_url).json()["text"])

    def test_a_job_the_queue_no_longer_holds_is_finished(self):
        """`prune_db_task_results` clears finished results after a fortnight, so a job the
        queue cannot answer for is old rather than running -- and a page polling one must
        stop rather than ask for ever."""
        body = self.get(self.user, self.tail_url).json()

        self.assertTrue(body["finished"])
        self.assertEqual("No longer on the queue", body["status"])

    def test_the_page_offers_no_checkbox_for_a_finished_job(self):
        self.write("done")

        self.assertNotIn('id="job-log-follow"', self.get(self.user).content.decode())


@override_settings(TASKS=DATABASE_BACKEND)
class RunningLogTailTestCase(TestCase):
    """A job the queue really is holding.

    **Against the database backend, and that is the whole point of this class.** Under the
    suite's own settings `ImmediateBackend.supports_get_result` is False, so every status
    reads `unknown` and *every* job reads as finished -- a test of "keeps polling while it
    runs" written without this override passes while asserting nothing at all.
    """

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create(username="asker", email="a@e.com", is_active=True)
        self.client.force_login(self.user)

    def _queued(self):
        from mutint_import import tasks as import_tasks
        from mutint_jobs import jobs as jobs_api

        return jobs_api.enqueue(import_tasks.build_coverage, 123456789, user=self.user)

    def test_a_running_job_that_has_printed_nothing_still_gets_a_box_and_a_script(self):
        """**The case live tailing exists for**, and the one the obvious gate would have
        missed. Clicking through to the log of a breseq run within a second of launching it
        finds no log at all -- so a page that renders the box and the script only when there
        is one would leave that reader on a static page that never polls, with a manual
        reload as the only way out."""
        job = self._queued()

        body = self.client.get("/jobs/%d/log" % job.pk).content.decode()

        self.assertIn('id="job-log"', body)
        self.assertIn('id="job-log-state"', body)
        self.assertIn("has not printed anything yet", body)

    def test_a_finished_job_that_printed_nothing_gets_neither(self):
        """Nothing will ever arrive, so there is nothing to poll for."""
        from mutint_jobs.models import Job

        job = Job.objects.create(task_result_id="gone", task_path="app.tasks.thing",
                                 user=self.user, label="Over")

        body = self.client.get("/jobs/%d/log" % job.pk).content.decode()

        self.assertNotIn('id="job-log"', body)
        self.assertNotIn('id="job-log-state"', body)
        self.assertIn("never will", body)

    def test_a_queued_job_is_not_finished_and_the_page_offers_the_checkbox(self):
        job = self._queued()
        with logs.open_log(job.task_result_id) as log:
            logs.write(log, "started")

        body = self.client.get("/jobs/%d/log/tail" % job.pk).json()

        self.assertFalse(body["finished"])
        self.assertEqual("Queued", body["status"])
        self.assertIn("started", body["text"])
        self.assertIn('id="job-log-follow"',
                      self.client.get("/jobs/%d/log" % job.pk).content.decode())

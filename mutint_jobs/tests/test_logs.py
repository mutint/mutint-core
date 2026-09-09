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
        self.write("brief")

        self.assertNotIn("Showing the last", self.get(self.user).content.decode())

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

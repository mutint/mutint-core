"""The annotation panel's answer: is a reference annotator still rewriting this annotation.

**Everything here runs against the database task backend**, and that is not tidiness. Under
the immediate backend `supports_get_result` is False, so `queue.status_of` answers
`STATUS_UNKNOWN` for every job, `jobs.finished` is therefore True, and `busy` is structurally
always False. That answer is *correct* there -- an immediate backend really did finish the
work inside the request that enqueued it -- which is exactly why a test written without the
override would pass while asserting nothing.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_experiment.models import Experiment, Project
from mutint_experiment.permissions import ROLE_READ, grant_project_access
from mutint_import import tasks as import_tasks
from mutint_import.annotation_status import status_for
from mutint_jobs import jobs as jobs_api

DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


@override_settings(TASKS=DATABASE_BACKEND)
class StatusForTestCase(TestCase):
    def setUp(self):
        from django_tasks_db.models import DBTaskResult
        self.DBTaskResult = DBTaskResult
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.colleague = User.objects.create(username="colleague", email="c@e.com",
                                             is_active=True)
        self.project = Project.objects.create(name="P", user=self.owner)
        self.experiment = Experiment.objects.create(name="E", project=self.project)

    def _job(self):
        return jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.owner,
                                label="ISEScan — E", experiment=self.experiment,
                                cancellable=True, annotates_reference=True)

    def test_nothing_in_flight_is_not_busy(self):
        self.assertEqual({"busy": False, "stalled": None, "jobs": []},
                         status_for(self.experiment, self.owner))

    def test_a_queued_annotation_job_is_busy(self):
        job = self._job()
        status = status_for(self.experiment, self.owner)
        self.assertTrue(status["busy"])
        self.assertEqual([job.pk], [row["id"] for row in status["jobs"]])
        self.assertEqual("READY", status["jobs"][0]["status"])

    def test_the_owner_may_stop_it(self):
        self._job()
        self.assertTrue(status_for(self.experiment, self.owner)["jobs"][0]["cancellable"])

    def test_somebody_else_sees_that_it_exists_and_no_controls(self):
        """The deliberate widening of `may_view`, and its limit in one assertion.

        Standing to read a job's *log* comes from having asked for the job -- that rule is
        unchanged. What changes is that a control switched off in front of somebody owes them
        a reason, so a colleague learns the job's name, its state and whose it is, and gets
        neither the log nor the button.
        """
        self._job()
        row = status_for(self.experiment, self.colleague)["jobs"][0]
        self.assertEqual("ISEScan — E", row["label"])
        self.assertEqual("owner", row["user"])
        self.assertEqual("", row["log"])
        self.assertFalse(row["cancellable"])

    def test_it_says_nothing_about_a_stalled_queue_when_nothing_is_in_flight(self):
        """The hedge is an explanation for a button being off, so with no button held there
        is nothing for it to explain -- and every import page in the deployment would carry
        it, since the queue is one queue."""
        self.DBTaskResult.objects.all().delete()
        jobs_api.enqueue(import_tasks.build_coverage, 1, user=self.owner)
        self.assertIsNone(status_for(self.experiment, self.owner)["stalled"])

    def test_it_hedges_when_nothing_is_taking_work(self):
        from django.utils import timezone

        from mutint_jobs import queue

        job = self._job()
        self.DBTaskResult.objects.filter(id=job.task_result_id).update(
            enqueued_at=timezone.now()
            - timezone.timedelta(seconds=queue.STALLED_SECONDS + 5))
        self.assertTrue(status_for(self.experiment, self.owner)["stalled"])


@override_settings(TASKS=DATABASE_BACKEND)
class StatusEndpointTestCase(TestCase):
    """`/import/annotators/status`, gated exactly as `/import/annotate` is."""

    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        self.project = Project.objects.create(name="P", user=self.owner)
        # Creating the project already made `owner` its owner; the reader is the one
        # grant this test has to make.
        grant_project_access(self.project, self.reader, ROLE_READ)
        self.experiment = Experiment.objects.create(name="E", project=self.project)

    def _get(self, **params):
        params.setdefault("experiment_id", self.experiment.id)
        return self.client.get("/import/annotators/status", params)

    def test_an_editor_gets_the_status(self):
        self.client.force_login(self.owner)
        response = self._get()
        self.assertEqual(200, response.status_code)
        self.assertEqual({"busy", "stalled", "jobs"}, set(response.json()))

    def test_it_is_never_cached(self):
        """A polled URL a proxy may cache is a panel that freezes for one reader and for
        nobody else -- which gets blamed on the queue. `job_log_tail`'s rule."""
        self.client.force_login(self.owner)
        self.assertEqual("no-store", self._get()["Cache-Control"])

    def test_a_reader_is_refused(self):
        self.client.force_login(self.reader)
        self.assertEqual(403, self._get().status_code)

    def test_a_locked_experiment_is_refused_in_its_own_words(self):
        from django.utils import timezone

        self.experiment.locked_at = timezone.now()
        self.experiment.save(update_fields=["locked_at"])
        self.client.force_login(self.owner)
        response = self._get()
        self.assertEqual(403, response.status_code)
        self.assertIn("locked", response.json()["error"].lower())

    def test_an_unknown_experiment_is_a_404(self):
        self.client.force_login(self.owner)
        self.assertEqual(404, self._get(experiment_id=999999).status_code)

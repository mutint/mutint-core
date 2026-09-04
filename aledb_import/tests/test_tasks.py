"""Coverage is derived off the request, and the queue it goes through is the real one.

Two different claims, and both need making. The suite runs tasks inline
(`AledbTestRunner.TASKS`), which is right for every *other* test -- one asserting what an
import produced should not also have to run a worker -- but it means nothing here exercises
the backend that production uses. So this module overrides that back and drives
`django_tasks_db` for real: enqueue writes a row and runs nothing, and the worker is what
runs it.

The failure this guards against is quiet in a specific way. If the enqueue is wrong -- a bad
signature, an argument that will not serialise, a backend that is not installed -- an import
still succeeds, because coverage was always best-effort. What you get is every sample
silently arriving without a BigWig.
"""

from django.test import TestCase, override_settings
from django.tasks import TaskResultStatus, task_backends

from aledb_import import tasks

DATABASE_BACKEND = {
    "default": {"BACKEND": "django_tasks_db.DatabaseBackend"},
}


class ImmediateBackendTestCase(TestCase):
    """What the rest of the suite runs under: enqueue means run, here and now."""

    def test_a_missing_sample_is_not_an_error(self):
        """A sample deleted between enqueue and execution has nothing to derive, which is
        not a failure -- and is exactly what a real queue makes possible for the first time."""
        result = tasks.build_coverage.enqueue(123456789)

        self.assertEqual(TaskResultStatus.SUCCESSFUL, result.status)
        self.assertIsNone(result.return_value)


@override_settings(TASKS=DATABASE_BACKEND)
class DatabaseBackendTestCase(TestCase):
    """The backend a deployment actually runs."""

    def setUp(self):
        from django_tasks_db.models import DBTaskResult
        self.DBTaskResult = DBTaskResult

    def test_enqueueing_stores_the_task_and_runs_nothing(self):
        """The whole point of moving it off the request: the POST returns before the work."""
        result = tasks.build_coverage.enqueue(123456789)

        self.assertEqual(TaskResultStatus.READY, result.status)
        row = self.DBTaskResult.objects.get()
        self.assertEqual("aledb_import.tasks.build_coverage", row.task_path)
        self.assertEqual([[123456789], {}], row.args_kwargs["args_kwargs"]
                         if "args_kwargs" in row.args_kwargs else
                         [row.args_kwargs["args"], row.args_kwargs["kwargs"]])

    def test_the_pk_is_what_travels(self):
        """Task arguments are serialised to JSON, so a model instance cannot make the trip.
        Passing one fails at enqueue -- loudly here, and invisibly in an import, where the
        exception would be swallowed along with the rest of coverage's best-effort contract.
        """
        from aledb_seq.models import Sample

        with self.assertRaises(Exception):
            tasks.build_coverage.enqueue(Sample())

    def test_the_worker_runs_a_stored_task(self):
        """End to end through `db_worker`'s own execution path, rather than by calling the
        function -- that is the part that would still be untested otherwise."""
        from django_tasks_db.management.commands.db_worker import Worker

        tasks.build_coverage.enqueue(123456789)
        row = self.DBTaskResult.objects.get()

        worker = Worker(queue_names=["default"], interval=0, batch=True,
                        backend_name="default", startup_delay=False, max_tasks=1,
                        worker_id="test", excluded_queue_names=[])
        worker.run_task(row)

        row.refresh_from_db()
        self.assertEqual(TaskResultStatus.SUCCESSFUL, row.status)

    def test_the_backend_is_the_one_configured(self):
        """Resolved through `task_backends`, not `default_task_backend`, which is a lazy
        connection proxy -- asking it for its type answers ConnectionProxy."""
        backend = task_backends["default"]

        self.assertEqual("django_tasks_db.backend.DatabaseBackend",
                         "%s.%s" % (type(backend).__module__, type(backend).__name__))

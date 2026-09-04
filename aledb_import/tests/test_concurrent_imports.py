"""Every sample must land, even while somebody else is uploading.

An LTEE drop lost five samples to `database is locked`, and the cause was not slowness:
`atomic()` issued a deferred `BEGIN`, `get_or_create` read before it wrote, and SQLite
refused that read-to-write upgrade outright -- without consulting `busy_timeout` -- as soon
as another connection had written in between.

**That particular failure is gone with SQLite**, and the two mechanisms built around it are
not, because what they protect against is not backend-specific:

    aledb_import.import_lock   one import at a time, so imports never contend at all
    aledb_import.retry         a sample that loses to some *other* writer tries again

`import_lock` earns its place more clearly on PostgreSQL than it did on SQLite, and the
reason is worth keeping in view: it is the only thing making the suite's unconstrained
`get_or_create` calls -- Experiment, Media, Isolate -- and
`_next_isolate_number`'s unlocked read-then-write unreachable by two importers at once.
SQLite's single-writer lock used to hide that; MVCC does not. Do not remove it on the
grounds that its original SQLite justification has expired.

`ImportLockVisibilityTestCase` is a `TransactionTestCase` on purpose and cannot be anything
else: a plain `TestCase` wraps every test in one transaction on one connection, so it can no
more exhibit a committed row another connection can see than it can exhibit a commit.
"""

import os
import threading

from django.db import OperationalError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from aledb_import import import_lock, retry
from aledb_import.models import ImportLock


def pg_error(sqlstate, message="server said so"):
    """A Django OperationalError shaped the way psycopg's arrives.

    Django wraps the driver's exception, so the SQLSTATE is on `__cause__` rather than on the
    error the caller catches.
    """
    class DriverError(Exception):
        pass

    cause = DriverError(message)
    cause.sqlstate = sqlstate
    error = OperationalError(message)
    error.__cause__ = cause
    return error


class LockErrorRecognitionTestCase(TestCase):
    """What counts as contention. Deliberately narrow: a malformed file fails the same way
    every time, and retrying it five times turns a clear message into a slow one."""

    def test_contention_sqlstates_are_recognised(self):
        for code in ("40001",     # serialization_failure
                     "40P01",     # deadlock_detected
                     "55P03"):    # lock_not_available
            self.assertTrue(retry.is_lock_error(pg_error(code)), code)

    def test_other_failures_are_not_retried(self):
        self.assertFalse(retry.is_lock_error(pg_error("23505")),   # unique_violation
                         "a duplicate key fails identically every time")
        self.assertFalse(retry.is_lock_error(pg_error("42703")),   # undefined_column
                         "a real error must not be retried")
        self.assertFalse(retry.is_lock_error(OperationalError("no sqlstate at all")))
        self.assertFalse(retry.is_lock_error(ValueError("40001")),
                         "only OperationalError is contention, whatever the text says")

    def test_the_message_is_not_what_is_matched(self):
        """The trap this module walked into once and must not again.

        Matching wording worked on SQLite and recognised *nothing* on PostgreSQL, so the
        retry read as live protection while doing nothing. Matching PostgreSQL's wording
        instead would be the same bug one step on: the server translates messages according
        to `lc_messages`, so a phrase match passes here and fails on a deployment running in
        another language. An error that merely says the word is not one.
        """
        self.assertFalse(
            retry.is_lock_error(OperationalError("deadlock detected")),
            "recognised on the strength of its text, which does not survive lc_messages")

    def test_a_real_error_is_raised_at_once(self):
        attempts = []

        def work():
            attempts.append(1)
            raise pg_error("42703", "column banana does not exist")

        with self.assertRaises(OperationalError):
            retry.with_retry(work, sleep=lambda _s: None)
        self.assertEqual(len(attempts), 1, "a real error must not be retried")


class RetryTestCase(TestCase):
    def test_a_locked_sample_succeeds_on_a_later_attempt(self):
        attempts = []

        def work():
            attempts.append(1)
            if len(attempts) < 3:
                raise pg_error("40001")
            return "imported"

        result = retry.with_retry(work, describe="s1", sleep=lambda _s: None)

        self.assertEqual(result, "imported")
        self.assertEqual(len(attempts), 3)

    def test_a_sample_that_never_gets_the_lock_is_still_reported(self):
        """The honest ceiling: what cannot be written is surfaced, not silently skipped.
        That is how the five lost samples were found in the first place."""
        def work():
            raise pg_error("40001")

        with self.assertRaises(OperationalError):
            retry.with_retry(work, attempts=3, sleep=lambda _s: None)

    def test_backoff_grows(self):
        delays = []

        def work():
            raise pg_error("40001")

        with self.assertRaises(OperationalError):
            retry.with_retry(work, attempts=4, sleep=delays.append)

        self.assertEqual(len(delays), 3, "no sleep after the final attempt")
        self.assertEqual(delays, sorted(delays))
        self.assertGreater(delays[-1], delays[0])


class ImportLockTestCase(TestCase):
    def test_a_second_acquire_is_refused_while_it_is_held(self):
        import_lock.acquire(holder="first")

        with self.assertRaises(import_lock.ImportInProgress) as caught:
            import_lock.acquire(holder="second")

        self.assertIn("already running", str(caught.exception))
        self.assertIn("first", str(caught.exception),
                      "the message should name who holds it")

    def test_releasing_lets_the_next_one_in(self):
        import_lock.acquire(holder="first")
        import_lock.release()

        import_lock.acquire(holder="second")   # must not raise
        self.assertEqual(import_lock.current().holder, "second")

    def test_hold_releases_even_when_the_import_raises(self):
        with self.assertRaises(RuntimeError):
            with import_lock.hold(holder="doomed"):
                raise RuntimeError("import blew up")

        self.assertIsNone(import_lock.current(),
                          "a failed import must not strand the lock")

    def test_a_stale_lock_is_taken_over(self):
        """A process killed mid-import cannot run its own cleanup, so nothing else ever
        could -- every later import would be refused until somebody deleted the row."""
        import_lock.acquire(holder="died")
        stale = timezone.now() - ImportLock.STALE_AFTER - timezone.timedelta(minutes=1)
        ImportLock.objects.filter(pk=import_lock.IMPORT_LOCK).update(acquired_at=stale)

        import_lock.acquire(holder="survivor")   # must not raise

        self.assertEqual(import_lock.current().holder, "survivor")

    def test_a_lock_just_short_of_stale_is_still_respected(self):
        import_lock.acquire(holder="slow but alive")
        nearly = timezone.now() - ImportLock.STALE_AFTER + timezone.timedelta(minutes=5)
        ImportLock.objects.filter(pk=import_lock.IMPORT_LOCK).update(acquired_at=nearly)

        with self.assertRaises(import_lock.ImportInProgress):
            import_lock.acquire(holder="impatient")


class BackendTestCase(TestCase):
    """The project actually running must be on the database it was configured for.

    **The earlier form of this test earned its place immediately**, and the failure it caught
    is still reachable. MutInt's `config/settings_local.py` replaced the whole `DATABASES`
    dict, so the assembled project -- the one people import into, and the one that lost the
    five samples -- ran on a different backend configuration from the one aledb-core had
    moved to. `./mutint check` passed throughout; only asserting on the live connection in
    the assembled project found it. An assembled project can still override `DATABASES`, and
    the symptom would still be silent.
    """

    def test_the_running_connection_is_postgresql(self):
        self.assertEqual("postgresql", connections["default"].vendor,
                         "this project overrode DATABASES and is not on PostgreSQL")

    def test_it_is_the_database_this_checkout_manages(self):
        """Skipped against somebody else's server, which is a legitimate way to run."""
        managed = os.environ.get("ALEDB_DB_MANAGED") == "1"
        if not managed:
            self.skipTest("running against an external server")
        self.assertEqual(os.environ["ALEDB_DB_HOST"],
                         connections["default"].settings_dict["HOST"],
                         "settings are not pointing at the cluster the entry script started")


class FinalizeUnderLockTestCase(TestCase):
    """The lock, through the endpoint that actually takes it."""

    def setUp(self):
        import shutil
        import tempfile

        from django.contrib.auth.models import User
        from django.test import override_settings

        from aledb_experiment.models import Project
        from aledb_experiment.views import _create_experiment

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create(username="locker", email="l@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)
        project = Project.objects.create(name="lock project", user=self.user)
        self.experiment = _create_experiment(project, "lock exp", self.user)

    def _staged_upload(self):
        import json
        import os
        import shutil
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        from aledb_import import breseq_folder
        from aledb_import.tests import breseq_fixture

        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        breseq_fixture.write_sample(source, "s1")

        entries = []
        for relative in breseq_folder.SAMPLE_FILES:
            path = os.path.join(source, "s1", relative)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    entries.append(("s1/" + relative.replace(os.sep, "/"), handle.read()))

        created = self.client.post(
            "/import/uploads/",
            data=json.dumps({"ale_experiment_id": self.experiment.id,
                             "import_type": "breseq_folder",
                             "files": [{"path": p, "size": len(b)} for p, b in entries]}),
            content_type="application/json")
        upload_id = created.json()["upload_id"]
        for path, payload in entries:
            self.client.post("/import/uploads/%s/chunk" % upload_id,
                             {"path": path, "offset": "0",
                              "chunk": SimpleUploadedFile("chunk", payload)})
        return upload_id

    def test_a_second_import_is_refused_while_one_is_running(self):
        upload_id = self._staged_upload()
        import_lock.acquire(holder="another machine pid 999")

        response = self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", response.json()["error"])
        self.assertIn("another machine", response.json()["error"],
                      "the refusal should say who to wait for")
        import_lock.release()

    def test_the_staged_files_survive_a_refusal(self):
        """Refused, not failed: the drop is untouched, so trying again costs only the button
        rather than re-uploading gigabytes."""
        from aledb_common import store

        upload_id = self._staged_upload()
        import_lock.acquire(holder="somebody")
        self.client.post("/import/uploads/%s/finalize" % upload_id, {})
        import_lock.release()

        self.assertTrue(os.path.isdir(store.staging_dir(upload_id)))

        response = self.client.post("/import/uploads/%s/finalize" % upload_id, {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreater(response.json()["total_mutations"], 0)

    def test_the_lock_is_released_when_the_import_finishes(self):
        upload_id = self._staged_upload()

        self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        self.assertIsNone(import_lock.current(),
                          "a finished import must not hold the lock")

    def test_the_lock_is_released_when_the_import_fails(self):
        from aledb_import import breseq_folder as folder_module

        upload_id = self._staged_upload()
        original = folder_module.run_post_processing
        folder_module.run_post_processing = lambda _e: (_ for _ in ()).throw(
            RuntimeError("rebuild exploded"))
        self.addCleanup(setattr, folder_module, "run_post_processing", original)

        response = self.client.post("/import/uploads/%s/finalize" % upload_id, {})

        self.assertEqual(response.status_code, 500)
        self.assertIsNone(import_lock.current(),
                          "a failed import must not strand the lock for everyone else")


class PollMustNotWriteTestCase(TestCase):
    """The progress poll has to be a pure reader, and this is why.

    Under WAL a reader never blocks and is never blocked -- so a poll that only reads returns
    instantly however long the import's transaction is. A poll that *writes* has to wait for
    the write lock, which the importer holds for the whole of each sample under
    `BEGIN IMMEDIATE`. The page then updates once per sample instead of continuously, which
    on a 20-30 sample GenomeDiff drop looks like the progress bar refreshing every few
    seconds and the table arriving late.

    `SESSION_SAVE_EVERY_REQUEST` is what made it a writer: it saves the session on *every*
    request, so each poll wrote a `django_session` row it had no need to.
    """

    def setUp(self):
        import shutil
        import tempfile

        from django.contrib.auth.models import User
        from django.test import override_settings

        from aledb_experiment.models import Project
        from aledb_experiment.views import _create_experiment
        from aledb_import.models import UploadSession

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create(username="poller", email="p@e.com", is_active=True)
        self.user.set_password("pw")
        self.user.save()
        self.client.force_login(self.user)
        project = Project.objects.create(name="poll project", user=self.user)
        experiment = _create_experiment(project, "poll exp", self.user)
        self.session = UploadSession.objects.create(
            user=self.user, experiment=experiment,
            import_type="genomediff", manifest=[], declared_bytes=0)

    def test_polling_progress_issues_no_writes(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                "/import/uploads/%s/progress" % self.session.id)

        self.assertEqual(response.status_code, 200)
        writes = [q["sql"] for q in queries.captured_queries
                  if q["sql"].strip().split()[0].upper()
                  in ("INSERT", "UPDATE", "DELETE")]
        self.assertEqual(
            writes, [],
            "a poll that writes waits for the import's write lock, so progress only "
            "updates once per sample: %s" % writes[:2])

    def test_ordinary_pages_still_save_their_session(self):
        """The opt-out is for the poll and nothing else. Turning
        SESSION_SAVE_EVERY_REQUEST off globally would have fixed the poll by changing when
        everybody gets logged out, which is not a trade worth making for one endpoint."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.client.get("/ale/experiments/")

        session_writes = [q["sql"] for q in queries.captured_queries
                          if "django_session" in q["sql"]
                          and q["sql"].strip().split()[0].upper() != "SELECT"]
        self.assertTrue(session_writes,
                        "a normal page must still have its session kept alive")


class ImportLockVisibilityTestCase(TransactionTestCase):
    """The lock has to be visible to another *process*, not just another thread.

    A `threading.Lock` would satisfy a naive test -- a second thread would be refused -- while
    protecting nothing against the second gunicorn worker or a `./aledb` command. What makes
    it real is that the claim is a committed database row, so this asserts exactly that.
    """

    def test_the_lock_is_a_committed_row_another_connection_can_see(self):
        import_lock.acquire(holder="holder")
        self.addCleanup(import_lock.release)

        seen = []

        def look():
            # A different thread gets its own connection, so this read crosses the
            # boundary an in-process lock would not.
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT holder FROM aledb_import_importlock WHERE name = %s",
                        [import_lock.IMPORT_LOCK])
                    seen.append(cursor.fetchone())
            finally:
                connections.close_all()

        thread = threading.Thread(target=look)
        thread.start()
        thread.join()

        self.assertEqual(seen, [("holder",)],
                         "the lock must be a row, not an object in one process's memory")

    def test_a_second_caller_is_refused_across_connections(self):
        import_lock.acquire(holder="holder-thread")
        self.addCleanup(import_lock.release)
        refused = []

        def contend():
            try:
                import_lock.acquire(holder="other-thread")
                refused.append(False)
            except import_lock.ImportInProgress:
                refused.append(True)
            finally:
                connections.close_all()

        thread = threading.Thread(target=contend)
        thread.start()
        thread.join()

        self.assertEqual(refused, [True])

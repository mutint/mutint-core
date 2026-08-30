"""Every sample must land, even while somebody else is uploading.

An LTEE drop lost five samples to `database is locked`, and the cause was not slowness: Django
4.2's `atomic()` issues a deferred `BEGIN`, `get_or_create` reads before it writes, and SQLite
refuses that read-to-write upgrade outright -- without consulting `busy_timeout` -- as soon as
another connection has written in between.

Three things answer it, and the point of this module is that **none of them is sufficient
alone**:

    aledb_common.db.sqlite_immediate   BEGIN IMMEDIATE, so a contender waits instead of failing
    aledb_import.import_lock           one import at a time, so imports do not contend at all
    aledb_import.retry                 a sample that loses to some *other* writer tries again

`ConcurrentWriterTestCase` is a `TransactionTestCase` on purpose and cannot be anything else: a
plain `TestCase` wraps every test in one transaction on one connection, so it can no more
exhibit lock contention than it can exhibit a commit.
"""

import os
import threading

from django.db import OperationalError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from aledb_import import import_lock, retry
from aledb_import.models import ImportLock


class LockErrorRecognitionTestCase(TestCase):
    """What counts as contention. Deliberately narrow: a malformed file fails the same way
    every time, and retrying it five times turns a clear message into a slow one."""

    def test_lock_messages_are_recognised(self):
        for message in ("database is locked",
                        "database table is locked: aledb_seq_mutation",
                        "Lock wait timeout exceeded; try restarting transaction",
                        "Deadlock found when trying to get lock"):
            self.assertTrue(retry.is_lock_error(OperationalError(message)), message)

    def test_other_failures_are_not_retried(self):
        self.assertFalse(retry.is_lock_error(OperationalError("no such table: nope")))
        self.assertFalse(retry.is_lock_error(ValueError("database is locked")),
                         "only OperationalError is contention, whatever the text says")

    def test_a_real_error_is_raised_at_once(self):
        attempts = []

        def work():
            attempts.append(1)
            raise OperationalError("no such column: banana")

        with self.assertRaises(OperationalError):
            retry.with_retry(work, sleep=lambda _s: None)
        self.assertEqual(len(attempts), 1, "a real error must not be retried")


class RetryTestCase(TestCase):
    def test_a_locked_sample_succeeds_on_a_later_attempt(self):
        attempts = []

        def work():
            attempts.append(1)
            if len(attempts) < 3:
                raise OperationalError("database is locked")
            return "imported"

        result = retry.with_retry(work, describe="s1", sleep=lambda _s: None)

        self.assertEqual(result, "imported")
        self.assertEqual(len(attempts), 3)

    def test_a_sample_that_never_gets_the_lock_is_still_reported(self):
        """The honest ceiling: what cannot be written is surfaced, not silently skipped.
        That is how the five lost samples were found in the first place."""
        def work():
            raise OperationalError("database is locked")

        with self.assertRaises(OperationalError):
            retry.with_retry(work, attempts=3, sleep=lambda _s: None)

    def test_backoff_grows(self):
        delays = []

        def work():
            raise OperationalError("database is locked")

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
    """The backend overrides a private method of a vendored Django class -- exactly what an
    upgrade breaks quietly. These two make that loud instead."""

    def test_transactions_begin_immediate(self):
        """Asserted against a stub rather than the live connection, which is already inside
        `TestCase`'s own transaction and answers `cannot start a transaction within a
        transaction`. The statement is the whole of what this module does, so the statement
        is what to pin."""
        from aledb_common.db.sqlite_immediate.base import DatabaseWrapper

        issued = []

        class FakeCursor:
            def execute(self, sql):
                issued.append(sql)

        wrapper = object.__new__(DatabaseWrapper)   # no connection wanted or needed
        wrapper.cursor = FakeCursor

        DatabaseWrapper._start_transaction_under_autocommit(wrapper)

        self.assertEqual(issued, ["BEGIN IMMEDIATE"],
                         "a deferred BEGIN is what lost samples to `database is locked`")

    def test_django_still_has_the_hook_this_overrides(self):
        """The tripwire. If a Django upgrade renames or drops
        `_start_transaction_under_autocommit`, the override silently stops applying and every
        transaction quietly goes back to deferred -- with no error anywhere. Delete this
        module at Django 5.1 and use `OPTIONS={'transaction_mode': 'IMMEDIATE'}` instead."""
        from django.db.backends.sqlite3 import base as sqlite_base

        self.assertTrue(
            hasattr(sqlite_base.DatabaseWrapper, "_start_transaction_under_autocommit"),
            "Django moved the hook; aledb_common.db.sqlite_immediate no longer applies")

    def test_the_running_connection_is_the_immediate_backend(self):
        """Asserted on the live wrapper, not on the ENGINE string, because what matters is
        the class actually in use.

        **This test earned its place immediately.** MutInt's `config/settings_local.py`
        replaced the whole `DATABASES` dict with a hardcoded
        `django.db.backends.sqlite3` and no OPTIONS -- so the assembled project, the one
        people import into and the one that lost the five samples, ran on the stock backend
        with a 5s timeout while aledb-core had moved on. `./mutint check` passed throughout;
        only running this in the assembled project found it.
        """
        from aledb_common.db.sqlite_immediate.base import DatabaseWrapper

        wrapper = connections["default"]
        if wrapper.vendor != "sqlite":
            self.skipTest("not running on SQLite")
        self.assertIsInstance(
            wrapper, DatabaseWrapper,
            "this project overrode ENGINE and is running deferred transactions")


class SqliteContentionTestCase(TestCase):
    """Why the backend exists, demonstrated against a real file database.

    **This cannot be done through the Django test database.** The suite runs on
    `file:memorydb_default?mode=memory&cache=shared`, and shared-cache SQLite locks whole
    tables under different rules than a file does -- so the deferred-transaction failure this
    guards against does not reproduce there. A test that ran two ORM writers against the test
    database would pass whether or not the fix were present, which is worse than no test.

    So this drives sqlite3 directly, over a temp file, in the two transaction modes. It pins
    the premise: deferred loses writes under contention and IMMEDIATE does not. `BackendTestCase`
    pins the other half -- that this application actually asks for IMMEDIATE.
    """

    def _run(self, begin, rounds=60, workers=2):
        import os
        import shutil
        import sqlite3
        import tempfile

        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "contended.sqlite3")

        setup = sqlite3.connect(path)
        setup.execute("PRAGMA journal_mode=WAL;")
        setup.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, k TEXT UNIQUE)")
        setup.commit()
        setup.close()

        lost = []

        def writer(tag):
            handle = sqlite3.connect(path, timeout=5, isolation_level=None)
            for i in range(rounds):
                try:
                    handle.execute(begin)
                    handle.execute("SELECT COUNT(*) FROM sample").fetchone()
                    handle.execute("INSERT INTO sample (k) VALUES (?)", ("%s-%d" % (tag, i),))
                    handle.execute("COMMIT")
                except sqlite3.OperationalError as exc:
                    lost.append(str(exc))
                    try:
                        handle.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
            handle.close()

        threads = [threading.Thread(target=writer, args=("w%d" % n,))
                   for n in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        check = sqlite3.connect(path)
        landed = check.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        check.close()
        return landed, lost, rounds * workers

    def test_deferred_transactions_lose_writes(self):
        """The bug, reproduced. Not an assertion that it always fails -- it is a race -- but
        that it *can*, which a guarantee cannot tolerate."""
        landed, lost, offered = self._run("BEGIN")

        self.assertTrue(lost, "expected contention to refuse at least one deferred upgrade")
        self.assertLess(landed, offered)
        self.assertTrue(any("locked" in message for message in lost), lost[:3])

    def test_immediate_transactions_lose_nothing(self):
        landed, lost, offered = self._run("BEGIN IMMEDIATE")

        self.assertEqual(lost, [])
        self.assertEqual(landed, offered, "every write must land")


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
            data=json.dumps({"ale_experiment_id": self.experiment.ale_id,
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

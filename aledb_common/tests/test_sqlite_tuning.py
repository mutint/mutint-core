"""The pragmas that let the Add page poll while an import writes.

Reproduced before this existed: with `journal_mode=delete` and the default 5s busy timeout,
a sample whose write transaction outlives the timeout blocks the *first* poll outright --
SQLite's rollback journal locks readers out of a database being written, so it is not a
matter of waiting a little longer.
"""

from unittest import mock

from django.db import connection
from django.db.backends.signals import connection_created
from django.test import TestCase

from aledb_common import sqlite_tuning


class SqliteTuningTestCase(TestCase):
    def test_the_busy_timeout_is_applied_to_the_live_connection(self):
        """The half that covers writer-against-writer: the poll's session row and the
        import's next sample are two writes, and WAL still serialises those."""
        if connection.vendor != "sqlite":
            self.skipTest("not running on SQLite")
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout;")
            self.assertEqual(cursor.fetchone()[0], sqlite_tuning.BUSY_TIMEOUT_MS)

    def test_the_receiver_is_connected(self):
        receivers = [
            r() for _key, r in connection_created.receivers
            if r() is not None]
        self.assertIn(sqlite_tuning.tune_sqlite_connection, receivers,
                      "nothing would set WAL if AppConfig.ready stopped importing it")

    def test_a_non_sqlite_connection_is_left_alone(self):
        """Production may not be SQLite, and these pragmas are not portable -- MySQL would
        raise on the first one. The vendor check is what keeps this out of its way."""
        other = mock.Mock()
        other.vendor = "mysql"

        sqlite_tuning.tune_sqlite_connection(sender=None, connection=other)

        other.cursor.assert_not_called()

    def test_a_database_that_refuses_the_pragmas_still_opens(self):
        """Tuning is an optimisation. Failing to open a connection over one would be a
        worse bug than the contention it exists to fix."""
        hostile = mock.Mock()
        hostile.vendor = "sqlite"
        hostile.cursor.side_effect = RuntimeError("no pragmas for you")

        sqlite_tuning.tune_sqlite_connection(sender=None, connection=hostile)  # must not raise

    def test_wal_is_requested(self):
        """Asserted on the statements issued, not on the resulting journal_mode: the test
        database is in-memory, where SQLite answers `memory` and WAL is not available. The
        pragma still has to be sent, because the file databases every deployment uses are
        where it matters."""
        issued = []
        cursor = mock.MagicMock()
        cursor.execute.side_effect = lambda sql: issued.append(sql)
        fake = mock.Mock()
        fake.vendor = "sqlite"
        fake.cursor.return_value.__enter__ = mock.Mock(return_value=cursor)
        fake.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        sqlite_tuning.tune_sqlite_connection(sender=None, connection=fake)

        self.assertIn("PRAGMA journal_mode=WAL;", issued)
        self.assertIn("PRAGMA synchronous=NORMAL;", issued)
        self.assertTrue(any("busy_timeout" in sql for sql in issued))

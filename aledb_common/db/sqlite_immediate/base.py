"""SQLite, with transactions that begin as writers.

``atomic()`` on Django 4.2 issues a bare ``BEGIN`` -- a *deferred* transaction, which takes no
lock until its first statement and takes a **read** lock if that statement is a read.
``Mutation.objects.get_or_create`` reads before it writes, so an import's transaction reliably
starts as a reader and then asks to become a writer.

**SQLite refuses that upgrade the moment another connection has written in between, and does
not consult ``busy_timeout`` when it does.** The refusal is deliberate on SQLite's part --
waiting could deadlock two readers each wanting to upgrade -- so it is reported immediately as
``SQLITE_BUSY``, surfacing here as ``database is locked``. No timeout and no journal mode can
reach it: WAL fixes readers being blocked by writers, which is a different thing.

Measured, three concurrent importers, WAL, 30s busy timeout, one transaction per sample:

    deferred BEGIN      42 of 150 samples landed
    BEGIN IMMEDIATE    150 of 150

``BEGIN IMMEDIATE`` takes the write lock up front, so a contending writer waits out
``busy_timeout`` instead of being refused mid-transaction.

**Delete this module when the suite reaches Django 5.1.** It is a subclass overriding a private
method of a vendored backend, which is exactly the sort of thing a Django upgrade breaks
quietly. 5.1 exposes the same behaviour supported, as::

    'OPTIONS': {'transaction_mode': 'IMMEDIATE'}

4.2 has no such setting -- ``transaction_mode`` appears nowhere in its sqlite3 backend -- which
is the whole reason this exists. ``aledb_common/tests/test_sqlite_immediate.py`` asserts the
statement actually issued, so an upgrade that moves the method underneath this fails loudly
rather than silently reverting to deferred transactions.

**This is not on its own a guarantee that every sample lands.** It stops the instant refusal; a
writer can still wait out ``busy_timeout`` and fail. What closes that is not holding two imports
at once (``aledb_import.import_lock``) and retrying a sample that loses anyway
(``aledb_import.retry``).
"""

from django.db.backends.sqlite3 import base


class DatabaseWrapper(base.DatabaseWrapper):
    """The stock SQLite backend, starting its transactions with the write lock held."""

    def _start_transaction_under_autocommit(self):
        # The one line this module exists for. The base class issues "BEGIN".
        self.cursor().execute("BEGIN IMMEDIATE")

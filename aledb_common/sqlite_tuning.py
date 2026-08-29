"""Make SQLite survive being read while it is being written.

SQLite ships in rollback-journal mode, where a writer locks the whole database against
readers and a reader locks it against writers. That is invisible while one request writes at
a time, and this repo did exactly that for years.

The Add page's import progress ended it. An import is a long run of write transactions --
one per sample, plus a BAM copy and a derived-data rebuild -- and the page now *polls* while
it runs. That is a second connection reading, and, because ``SESSION_SAVE_EVERY_REQUEST`` is
on, writing a session row on every poll. Two connections, one of them holding the database
for the length of a sample, and the other arriving several times a second: the loser waits
out the busy timeout and raises ``database is locked``. It was reported against a real
import, not found in a test.

Two pragmas fix it, and they fix different halves:

- **WAL** puts writers in a side log, so a reader never blocks a writer and a writer never
  blocks a reader. This is what makes polling during an import safe at all. It is a property
  of the database *file* and persists once set, so this runs on every connection only
  because it is the cheapest place to be sure of it -- re-issuing it is a no-op that returns
  the current mode. The ``-wal`` and ``-shm`` sidecars it creates are already gitignored in
  both this repo and every assembled project.
- **``busy_timeout``** covers what WAL does not: two *writers* still serialise, so the
  poll's session write and the import's next sample can still collide. Five seconds is
  sqlite3's default and is not much when the other writer is copying an alignment; thirty
  gives it room to wait rather than raise.

``synchronous=NORMAL`` is the documented companion to WAL: with a write-ahead log, fsyncing
every commit buys durability against power loss that a development database does not need,
and paying it per sample is most of what makes a large import feel slow. The window it opens
is narrow and worth naming: an **operating system crash or power cut** can lose recently
committed transactions, while an application crash cannot -- WAL still recovers those.

**The one deployment this rules out is a database on a network filesystem.** WAL coordinates
readers and writers through shared memory (the ``-shm`` file), which NFS and SMB do not
implement correctly, and SQLite either refuses WAL or corrupts under it there. Nothing in
this repo puts the database on a share -- ``base_settings`` points it beside the code -- but
a deployment that moved it to one would need this receiver disabled, and would be back to
serialising a poll against an import.

**MySQL and Postgres deployments are untouched** -- the receiver returns immediately unless
the connection's vendor is sqlite, so nothing here reaches production if production is not
SQLite.

Why a signal rather than ``OPTIONS['init_command']``: that is Django 5.1 and up, and this
runs on 4.2, where the sqlite3 backend accepts no such key. ``timeout`` *is* passed through
to ``sqlite3.connect`` on 4.2, so it is set in ``base_settings`` as well -- belt and braces,
since a connection made before this module is imported would otherwise keep the 5s default.
"""

import logging

from django.db.backends.signals import connection_created
from django.dispatch import receiver

logger = logging.getLogger("aledb_common.sqlite_tuning")

BUSY_TIMEOUT_MS = 30000


@receiver(connection_created)
def tune_sqlite_connection(sender, connection, **_kwargs):
    """Put every new SQLite connection into WAL with a generous busy timeout."""
    if connection.vendor != "sqlite":
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=%d;" % BUSY_TIMEOUT_MS)
            cursor.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        # A database that will not take these is still a usable database -- it is only
        # worse under concurrency. Failing to open it over a tuning pragma would be a much
        # bigger problem than the one this solves.
        logger.warning("could not apply SQLite tuning pragmas", exc_info=True)

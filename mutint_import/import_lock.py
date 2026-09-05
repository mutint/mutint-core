"""One import at a time, so every sample lands.

SQLite permits exactly one writer. Two imports running together therefore do not go faster --
they interleave their per-sample transactions and each one that cannot get the write lock
inside ``busy_timeout`` fails and loses its sample. `BEGIN IMMEDIATE`
(``OPTIONS={'transaction_mode': 'IMMEDIATE'}``) stops a transaction being *refused outright*, and
retrying (``mutint_import.retry``) absorbs a lost race, but neither is a guarantee: measured
with three importers and transactions longer than the timeout, ``IMMEDIATE`` alone landed 19
of 30 samples and ``IMMEDIATE`` plus five retries still lost one. Not running two at once
landed 30 of 30.

So this is the piece that closes it, and the other two exist to handle what remains: the
short writes from everything *else* touching the database -- every poll writes a session row,
because ``SESSION_SAVE_EVERY_REQUEST`` is on, and so does anyone else browsing.

**Acquisition is a row insert, not ``select_for_update``.** SQLite has no row locking, so
``select_for_update`` is a documented no-op there and would look like mutual exclusion while
providing none. Creating a row whose primary key is already taken raises ``IntegrityError`` on
every backend, so the winner is whoever gets the insert in -- which is exactly the semantics
wanted, and is portable.

The cost is visible and intended: a second import waits, or is told to come back. Since
SQLite was serializing these anyway, what changes is that the queue is orderly rather than a
race some samples lose.
"""

import logging
import os
import socket
from contextlib import contextmanager

from django.db import IntegrityError, transaction
from django.utils import timezone

from mutint_import.models import ImportLock

logger = logging.getLogger("mutint_import.import_lock")

# One lock for the whole database, not one per experiment. The contention is for SQLite's
# single writer, which two imports share no matter which experiments they target -- a
# per-experiment lock would read as finer-grained while protecting nothing.
IMPORT_LOCK = "import"


class ImportInProgress(Exception):
    """Somebody else is importing. Carries a sentence naming who and since when."""


def describe_holder():
    """Who this process is, for the message the loser sees."""
    return "%s pid %d" % (socket.gethostname(), os.getpid())


@contextmanager
def hold(name=IMPORT_LOCK, holder=None):
    """Hold the import lock for the block, or raise ``ImportInProgress``.

    Released in a ``finally``, so an import that raises does not strand it. A process that
    dies outright cannot run that, which is what ``ImportLock.is_stale`` is for.
    """
    acquire(name, holder)
    try:
        yield
    finally:
        release(name)


def acquire(name=IMPORT_LOCK, holder=None):
    """Take the lock, breaking a stale one. Raises ``ImportInProgress`` if it is held."""
    holder = holder or describe_holder()
    try:
        # Its own transaction: on failure this must leave nothing behind, and the caller's
        # import has not begun yet.
        with transaction.atomic():
            ImportLock.objects.create(name=name, holder=holder)
        return
    except IntegrityError:
        pass

    existing = ImportLock.objects.filter(pk=name).first()
    if existing is None:
        # Released between the insert failing and this read. The next attempt is the
        # honest answer rather than a guess about who won.
        return acquire(name, holder)

    if not existing.is_stale():
        raise ImportInProgress(
            "An import is already running (%s, started %s). Imports run one at a time -- "
            "the database takes one writer -- so wait for it to finish and try again."
            % (existing.holder or "unknown", timezone.localtime(
                existing.acquired_at).strftime("%H:%M:%S")))

    # Stale: whoever held it is gone. Take it over, but only if nobody else got there first,
    # so two processes finding the same stale lock cannot both proceed.
    claimed = ImportLock.objects.filter(
        pk=name, acquired_at=existing.acquired_at).update(
            holder=holder, acquired_at=timezone.now())
    if not claimed:
        raise ImportInProgress(
            "An import is already running. Wait for it to finish and try again.")
    logger.warning("took over a stale import lock held by %s since %s",
                   existing.holder, existing.acquired_at)


def release(name=IMPORT_LOCK):
    """Give the lock up. Never raises -- a failure here must not mask the import's own."""
    try:
        ImportLock.objects.filter(pk=name).delete()
    except Exception:
        logger.exception("could not release the import lock; it will go stale in %s",
                         ImportLock.STALE_AFTER)


def current(name=IMPORT_LOCK):
    """The lock row if one is held, else None. For `./mutint` and for tests."""
    return ImportLock.objects.filter(pk=name).first()

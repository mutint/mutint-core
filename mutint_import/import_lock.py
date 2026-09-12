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


@contextmanager
def hold_waiting(name=IMPORT_LOCK, holder=None, timeout=1800, poll=5, check=None):
    """Hold the import lock for the block, waiting for it rather than refusing.

    `hold` refuses immediately by design: the web path would rather answer 409 than hold a
    request open for somebody else's drop. A **background task** is the opposite case --
    nobody is waiting on a response, and what is at stake is hours of finished work that
    would be thrown away over a few seconds of overlap with a web upload. So this polls
    `acquire` every `poll` seconds for up to `timeout`, then raises the `ImportInProgress`
    it last saw.

    `check` is an optional callable asked before every attempt and expected to raise when
    the wait should be given up -- `mutint_jobs.jobs.check_cancelled` fits, and it is why
    this takes a callable rather than a task id: a wait of half an hour is long enough for
    somebody to change their mind, and a wait nobody can give up on is the same dead end as
    a job nobody can stop. This is mutint-breseq's `_wait_for_import_lock`, promoted.

    **A lock this process already holds is not waited on.** Under an immediate task backend
    -- the whole test suite, and any deployment that chooses one -- a task runs *inside* the
    request that enqueued it, and that request may hold the lock through `finalize_upload`
    or the Run annotators endpoint. Waiting on it would be waiting on ourselves, for the
    length of the timeout. The holder string carries this host and pid (`describe_holder`),
    so "held by this process" is answerable; such a hold yields at once and releases nothing,
    since the outer holder will. A worker is another process and is never mistaken for one.
    """
    import time

    own = describe_holder()
    existing = current(name)
    if existing is not None and existing.holder and existing.holder.endswith(own) \
            and not existing.is_stale():
        yield
        return

    holder = holder or own
    deadline = time.monotonic() + timeout
    while True:
        if check is not None:
            check()
        try:
            acquire(name, holder)
            break
        except ImportInProgress:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll)
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

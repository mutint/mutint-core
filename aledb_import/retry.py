"""Retry a sample that lost a race for the write lock.

The import lock stops two imports running at once, but it does not make an import the only
writer: every poll of the Add page writes a session row, because
``SESSION_SAVE_EVERY_REQUEST`` is on, and so does anybody else using the site. Those writes are
short, and they can still land on a sample's ``BEGIN`` and cost it the wait.

**Retrying a sample is safe, and that is a property of how imports are built rather than
something arranged here.** Each sample is its own ``transaction.atomic()``, so a failed one
rolls back whole -- the chain rows, the mutations, the observations and the delete that
preceded them all go together. And re-import is idempotent by design
(``_database_gd_mutations`` clears the sample's observations before writing its own), so an
attempt that got part-way leaves nothing for the next one to trip over.

What must *not* be retried is a real error. A malformed file, a reference mismatch or a locked
experiment will fail identically every time, and retrying them five times would turn a clear
message into a slow one. ``is_lock_error`` is deliberately narrow: it matches SQLite's and
MySQL's wording for lock contention and nothing else.

This is the third of three pieces and the smallest. See ``aledb_import.import_lock`` for why
none of them is sufficient alone.
"""

import logging
import time

from django.db import OperationalError

logger = logging.getLogger("aledb_import.retry")

ATTEMPTS = 5
BACKOFF_SECONDS = 0.2

# SQLite says "database is locked" / "database table is locked"; MySQL says "Lock wait timeout
# exceeded" and "Deadlock found". Matched on the message because the driver reports all of them
# as OperationalError, which also covers errors that must not be retried.
LOCK_PHRASES = (
    "database is locked",
    "database table is locked",
    "lock wait timeout",
    "deadlock found",
)


def is_lock_error(exc):
    """Whether `exc` is contention -- worth another go -- rather than a real failure."""
    if not isinstance(exc, OperationalError):
        return False
    return any(phrase in str(exc).lower() for phrase in LOCK_PHRASES)


def with_retry(work, describe="", attempts=ATTEMPTS, sleep=time.sleep):
    """Run `work()`, retrying it while it fails for want of the write lock.

    Returns whatever `work` returns. Re-raises the last exception once the attempts are
    spent, so a sample that genuinely cannot be written is still reported as failed rather
    than silently skipped -- which is what makes the residue visible.
    """
    for attempt in range(1, attempts + 1):
        try:
            return work()
        except Exception as exc:
            if not is_lock_error(exc) or attempt == attempts:
                raise
            delay = BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning("%s: database was locked (attempt %d of %d), retrying in %.1fs",
                           describe or "sample", attempt, attempts, delay)
            sleep(delay)

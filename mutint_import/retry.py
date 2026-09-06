"""Retry a sample that lost a race for the write lock.

The import lock stops two imports running at once, but it does not make an import the only
writer: every poll of the Import data page writes a session row, because
``SESSION_SAVE_EVERY_REQUEST`` is on, and so does anybody else using the site. Those writes are
short, and they can still land on a sample's ``BEGIN`` and cost it the wait.

**Retrying a sample is safe, and that is a property of how imports are built rather than
something arranged here.** Each sample is its own ``transaction.atomic()``, so a failed one
rolls back whole -- the chain rows, the mutations, the calls and the delete that
preceded them all go together. And re-import is idempotent by design
(``_database_gd_mutations`` clears the sample's calls before writing its own), so an
attempt that got part-way leaves nothing for the next one to trip over.

What must *not* be retried is a real error. A malformed file, a reference mismatch or a locked
experiment will fail identically every time, and retrying them five times would turn a clear
message into a slow one. ``is_lock_error`` is deliberately narrow: it recognizes contention and
nothing else.

**It matches SQLSTATE, not the message text, and that is the whole reason this module needed
attention when the backend changed.** It used to match SQLite's and MySQL's wording -- so on
PostgreSQL it recognized nothing at all, and every retry in the suite quietly stopped
retrying while still reading like live protection. Matching PostgreSQL's *wording* instead
would have been the same bug one step further on: the server translates error messages
according to ``lc_messages``, so a phrase match passes on the developer's machine and fails
on a deployment configured in another language. The five-character code is the part that
does not move.

This is the third of three pieces and the smallest. See ``mutint_import.import_lock`` for why
none of them is sufficient alone.
"""

import logging
import time

from django.db import OperationalError

logger = logging.getLogger("mutint_import.retry")

ATTEMPTS = 5
BACKOFF_SECONDS = 0.2

#: PostgreSQL class 40 (transaction rollback) plus lock_not_available. These are exactly the
#: failures where the same work, tried again, can succeed:
#:
#:   40001  serialization_failure  -- concurrent update; the classic "just try again"
#:   40P01  deadlock_detected      -- the server picked us as the victim
#:   55P03  lock_not_available     -- NOWAIT or lock_timeout gave up waiting
#:
#: Contention is *more* likely to be worth retrying here than it was on SQLite, not less:
#: MVCC produces serialization failures that a single-writer database structurally could not.
LOCK_SQLSTATES = frozenset(["40001", "40P01", "55P03"])


def sqlstate(exc):
    """The five-character SQLSTATE behind a Django database error, or None.

    Django wraps the driver's exception, so the code is on ``__cause__`` -- psycopg puts it on
    the exception itself as ``sqlstate``. Walked rather than assumed, because a caller may
    have re-raised.
    """
    seen = 0
    while exc is not None and seen < 5:
        code = getattr(exc, "sqlstate", None) or getattr(getattr(exc, "pgcode", None), "real", None)
        if isinstance(code, str) and code:
            return code
        code = getattr(exc, "pgcode", None)
        if isinstance(code, str) and code:
            return code
        exc = getattr(exc, "__cause__", None)
        seen += 1
    return None


def is_lock_error(exc):
    """Whether `exc` is contention -- worth another go -- rather than a real failure."""
    if not isinstance(exc, OperationalError):
        return False
    return sqlstate(exc) in LOCK_SQLSTATES


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

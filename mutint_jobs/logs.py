"""What a job's commands printed, kept where a person can read it.

`Job` records who asked for the work and what to call it; this records what the work *said*.
It is a file rather than a column, and that is the whole design:

- **A running job's log has to be readable.** The point of this is to open a breseq run three
  hours in and see how far it has got, so the output cannot arrive in one lump when the process
  exits. A subprocess writes straight into this file -- see `processes.run_tool` -- and the web
  process reads whatever is there.
- **A column cannot be appended to cheaply.** Every flush would be an UPDATE of the whole text,
  from a worker, for the length of the run.

**How promptly output appears is the child's choice, not ours.** breseq's stdout is a file
rather than a terminal, so it block-buffers, and a refresh shows whatever it has flushed. A
pipe would behave identically -- the buffering is on the far side of it -- and only a pty would
change that, which is not worth a job log.

The file lives at ``<store>/components/mutint_jobs/<task_result_id>/job.log``, which is
`store.component_dir`'s durable per-row work area. Nothing reaps it; the `post_delete` receiver
on `Job` does, which is that function's stated contract and what makes `./mutint reap_jobs`
clear logs along with the rows it strands.

**Keyed by the queue's result id, not by `Job.pk`**, and that is not a preference. A `Job` row
is created *after* `task.enqueue` returns, so under an immediate backend -- which is what the
whole test suite runs on -- the task executes before its row exists and a pk-keyed log would be
written nowhere, silently. The result id is the one handle that exists while the task is
running, which is also why `jobs.is_cancelled` takes it. A task learns its own from
`TaskContext`; see `mutint_breseq.tasks`.

**It is gzipped when the job finishes**, by the `task_finished` receiver in `apps.py`.
Uncompressed while running is what makes appending and tailing cheap; compressed afterwards is
what makes keeping them cheap. A worker killed outright never fires that signal, so every
reader here accepts either form.
"""

import gzip
import logging
import os
import shutil
from contextlib import contextmanager

from mutint_common import store

logger = logging.getLogger("mutint_jobs.logs")

#: This app's directory under ``<store>/components/``.
COMPONENT = "mutint_jobs"

LOG_NAME = "job.log"
COMPRESSED_NAME = LOG_NAME + ".gz"

#: What the page renders, from the end. A breseq log runs to megabytes and the news is at the
#: bottom; the download route has the whole thing, and the page says when it had to cut.
TAIL_BYTES = 256 * 1024

#: How much is read at a time when streaming the whole log to a client.
CHUNK_BYTES = 64 * 1024


def log_dir(task_result_id):
    """Where this job's log lives, or None when there is nothing to key it by.

    **An empty id is a real argument here, not a caller's mistake**: a task called directly,
    outside any queue, has none. Every reader below therefore has to mean "there is no log"
    rather than raise.
    """
    if not task_result_id:
        return None
    try:
        return store.component_dir(COMPONENT, task_result_id)
    except ValueError:
        # A result id that is not a plain token. Nothing the queue generates looks like this,
        # so it means somebody passed the wrong thing rather than that a log is missing.
        logger.warning("not a usable job log key: %r", task_result_id)
        return None


def log_path(task_result_id, compressed=False):
    """Where this job's log is, in one form or the other. Neither need exist."""
    directory = log_dir(task_result_id)
    if directory is None:
        return None
    name = COMPRESSED_NAME if compressed else LOG_NAME
    return os.path.join(directory, name)


def stored_path(task_result_id):
    """The log that is actually there, the **plain** one preferred, or None.

    Both can exist, in two ways, and preferring the plain file is right for each. While
    `compress` runs, the plain file is complete and untouched until the gzip beside it is
    finished, so a reader that takes it loses nothing. And a job that writes *again* after
    being compressed -- a retry, or a task called a second time against the same row -- opens
    a new plain file beside the old gzip, and its output is the current one. Preferring the
    gzip showed the previous run's log and hid the one that was happening.
    """
    for compressed in (False, True):
        path = log_path(task_result_id, compressed=compressed)
        if path is not None and os.path.isfile(path):
            return path
    return None


def exists(task_result_id):
    return stored_path(task_result_id) is not None


class _NullLog:
    """Stands in for a log when there is no `Job` to attach one to.

    A task may be enqueued the plain way, or called directly by a test, and neither has a row
    here. Writing to nowhere is a better answer than raising: a tool runner should not need a
    branch for whether anybody is keeping its output, and the alternative -- refusing to run --
    would make the log a prerequisite for the work rather than a record of it.
    """

    def write(self, data):
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


@contextmanager
def open_log(task_result_id):
    """Append to the log of the job with this queue result id.

    Takes the **same handle a task already passes to `jobs.is_cancelled`**, so a task needs
    nothing new to reach its own log, and no query: the id names the directory.

    Yields a binary file open for appending, or a `_NullLog` when there is no id or the file
    cannot be opened. Append rather than truncate, because a task may open this more than once
    -- fastp, then breseq -- and because a retried job should not erase what its first attempt
    said.
    """
    directory = log_dir(task_result_id)
    if directory is None:
        yield _NullLog()
        return

    try:
        store.ensure_dir(directory)
        handle = open(log_path(task_result_id), "ab")
    except OSError:
        # A log is commentary; the work is the point. This is the same posture
        # `jobs.is_cancelled` takes -- an unreadable side record must not fail the task.
        logger.warning("could not open the log for %s; continuing without one",
                       task_result_id, exc_info=True)
        yield _NullLog()
        return

    try:
        yield handle
    finally:
        try:
            handle.close()
        except OSError:
            pass


def write(log, text):
    """Write one line of a task's own commentary into an open log.

    The tools write themselves; this is for the sentences around them -- what was skipped and
    why, which stage is starting. Encoded here so callers deal in text and the file stays one
    stream of bytes with the subprocess output.
    """
    if not text.endswith("\n"):
        text += "\n"
    try:
        log.write(text.encode("utf-8", "replace"))
        log.flush()
    except (OSError, ValueError):
        logger.debug("could not write to a job log", exc_info=True)


def read_tail(task_result_id, tail_bytes=TAIL_BYTES):
    """The end of the log, as (text, truncated).

    `truncated` is what the page needs in order not to present a slice as if it were the whole
    log -- a "jump to the top" button over a silently cut log points at a beginning that is not
    one.
    """
    path = stored_path(task_result_id)
    if path is None:
        return "", False

    try:
        if path.endswith(".gz"):
            # No seeking to the end of a gzip without decompressing what precedes it, so this
            # reads the lot and keeps the tail. A job log is megabytes, not gigabytes.
            with gzip.open(path, "rb") as handle:
                data = handle.read()
            truncated = len(data) > tail_bytes
        else:
            # `truncated` is asked of the *file*, not of what came back: the seek below can
            # never return more than `tail_bytes`, so testing the length of `data` would say
            # "not cut" about every cut log there is.
            size = os.path.getsize(path)
            truncated = size > tail_bytes
            with open(path, "rb") as handle:
                handle.seek(max(0, size - tail_bytes))
                data = handle.read()
    except (OSError, EOFError, gzip.BadGzipFile):
        logger.warning("could not read the log for %s", task_result_id, exc_info=True)
        return "", False

    if truncated:
        data = data[-tail_bytes:]
    # `replace` for the same reason the subprocess output was decoded that way: a tool may emit
    # anything, and a log that refuses to render is worse than one with a � in it.
    text = data.decode("utf-8", "replace")
    if truncated:
        # A partial first line is worse than a missing one.
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1:]
    return text, truncated


def stream(task_result_id):
    """The whole log, in chunks, for the download route. Empty when there is none."""
    path = stored_path(task_result_id)
    if path is None:
        return

    opener = gzip.open if path.endswith(".gz") else open
    try:
        handle = opener(path, "rb")
    except (OSError, gzip.BadGzipFile):
        logger.warning("could not open the log for %s", task_result_id, exc_info=True)
        return

    with handle:
        while True:
            try:
                block = handle.read(CHUNK_BYTES)
            except (OSError, EOFError, gzip.BadGzipFile):
                return
            if not block:
                return
            yield block


def compress(task_result_id):
    """Gzip a finished job's log in place. Idempotent, and never raises.

    The compressed copy is written whole before the plain one is unlinked, so a reader in
    between finds one complete file either way.
    """
    plain = log_path(task_result_id)
    if plain is None or not os.path.isfile(plain):
        return
    target = log_path(task_result_id, compressed=True)
    try:
        with open(plain, "rb") as source, gzip.open(target, "wb") as destination:
            shutil.copyfileobj(source, destination, CHUNK_BYTES)
        os.remove(plain)
    except OSError:
        logger.warning("could not compress the log for %s", task_result_id, exc_info=True)


def discard(task_result_id):
    """Remove a job's log directory. For the `post_delete` receiver on `Job`."""
    directory = log_dir(task_result_id)
    if directory is None:
        return
    try:
        shutil.rmtree(directory, ignore_errors=True)
    except (ValueError, TypeError):
        logger.debug("no log directory to remove for an unsaved Job")


def orphans():
    """Log directories no `Job` row claims.

    Keying by the queue's id rather than by `Job.pk` buys correctness under every backend and
    costs this: work enqueued with a bare `task.enqueue` writes a log that no `post_delete`
    will ever reach, because there is no row to delete. `./mutint reap_jobs` sweeps them --
    the same command that clears the queue rows nothing else can.
    """
    from mutint_jobs.models import Job

    root = os.path.join(store.store_root(), "components", COMPONENT)
    try:
        present = set(os.listdir(root))
    except OSError:
        return []

    claimed = set(Job.objects.filter(
        task_result_id__in=present).values_list("task_result_id", flat=True))
    return sorted(present - claimed)

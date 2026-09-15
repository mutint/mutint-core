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

#: What a *row* stores as its own copy of the log -- `BreseqRun.log`, `IsescanRun.log` -- taken
#: from the end, because that is where a failure says why. Not what the log page renders: that
#: reads from an offset and keeps everything (see `read_since`).
TAIL_BYTES = 256 * 1024

#: The ceiling on one response from `read_since`, and so on what the log page holds in one
#: `<pre>`. Deliberately far above anything real -- a complete breseq run against REL606
#: measured 63 KB, and the largest of fifteen stored job logs was 251 KB -- because its job is
#: to stop a runaway tool taking the browser and this process down with it, not to trim
#: ordinary output. Past it the page repaints from the end and says so, which is the only
#: honest answer once content has to be dropped.
MAX_RENDER_BYTES = 8 * 1024 * 1024

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


def _whole_characters(data):
    """`data` with any trailing partial UTF-8 sequence removed.

    A byte offset can land in the middle of a character, and the next read starts exactly
    where this one stopped -- so stopping mid-sequence would decode to a replacement
    character *here* and another one *there*, turning one character into two mojibake blobs
    that no later read can repair. Dropping the partial tail leaves it for the next read,
    which sees the whole sequence.
    """
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte < 0x80:          # ASCII: nothing before it can be incomplete
            return data
        if byte >= 0xC0:         # a start byte: is its sequence all here?
            needed = 4 if byte >= 0xF0 else 3 if byte >= 0xE0 else 2
            return data if back >= needed else data[:-back]
        # 0x80..0xBF is a continuation byte; keep walking back to its start byte
    return data


def read_since(task_result_id, offset=0, max_bytes=None):
    """Log content from `offset` bytes in, as `(text, next_offset, reset, truncated)`.

    This is what the log page reads, and it reads by **offset** so that the page can keep
    everything it has ever been sent. Re-sending the tail on every poll -- which is what this
    replaced -- bounds the page at whatever that tail is: once the log outgrows it there is no
    common prefix to append onto, so the reader's own scrollback is thrown away and repainted
    with the end. Asking for "what is new since byte N" has no such ceiling, and costs less on
    the wire besides: an idle poll transfers nothing rather than a quarter of a megabyte.

    `offset` is into the log's *logical* content, which is the same whether the file on disk is
    the plain one or the gzip written when the job finished -- so an offset held across that
    switch stays valid, and a page watching a job that ends mid-poll does not skip or repeat.

    `reset` means **replace what you hold with this text** rather than appending it. Two things
    cause it and both are real: a log that has been replaced since (a task that writes again
    after its log was compressed opens a fresh file, so the caller's offset is past the end),
    and a gap too large to hand over in one response. `truncated` then says whether what came
    back starts at the beginning of the log -- the page must not offer a Top button onto a
    beginning that is not one.
    """
    # Resolved here rather than as a default argument, so a test can lower the ceiling by
    # patching the constant instead of writing eight megabytes to prove what happens past it.
    if max_bytes is None:
        max_bytes = MAX_RENDER_BYTES

    path = stored_path(task_result_id)
    if path is None:
        return "", 0, False, False

    try:
        if path.endswith(".gz"):
            # No seeking into a gzip without decompressing what precedes the offset anyway, so
            # this reads the lot. Only a finished job's log is compressed, so it happens once
            # per page rather than once per poll.
            with gzip.open(path, "rb") as handle:
                whole = handle.read()
            size = len(whole)
            start, reset = _window(offset, size, max_bytes)
            data = whole[start:]
        else:
            size = os.path.getsize(path)
            start, reset = _window(offset, size, max_bytes)
            with open(path, "rb") as handle:
                handle.seek(start)
                data = handle.read()
    except (OSError, EOFError, gzip.BadGzipFile):
        logger.warning("could not read the log for %s", task_result_id, exc_info=True)
        return "", 0, False, False

    data = _whole_characters(data)
    text = data.decode("utf-8", "replace")
    if reset and start > 0:
        # A partial first line is worse than a missing one -- `read_tail`'s rule, and for the
        # same reason: this text is about to become the whole of what the reader can see.
        newline = text.find("\n")
        if newline != -1:
            dropped = len(text[:newline + 1].encode("utf-8"))
            start += dropped
            text = text[newline + 1:]
    return text, start + len(text.encode("utf-8")), reset, reset and start > 0


def _window(offset, size, max_bytes):
    """Where to start reading, and whether that is a reset. See `read_since`."""
    # A negative or past-the-end offset is not a caller's bug to raise on: the log it refers to
    # is simply not the log on disk any more.
    if offset < 0 or offset > size:
        offset = 0
        reset = True
    else:
        reset = False
    if size - offset > max_bytes:
        return size - max_bytes, True
    return offset, reset


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

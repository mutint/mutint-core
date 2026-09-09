"""Running an external command as part of a job: cancellably, with its output kept.

This was `mutint_breseq.runner.run_breseq_process` and nothing about it was breseq's. What it
does is what any task shelling out from a worker needs -- poll the cancellation flag while the
tool runs, kill the whole process group when somebody asks, and put the output somewhere a
person can read -- so it lives beside the rest of the jobbing infrastructure and the plugin
keeps only its argv, its PATH and its idea of usable output.

    from mutint_jobs import logs, processes

    with logs.open_log(task_result_id) as log:
        code = processes.run_tool(argv, log, env=env, timeout=3600,
                                  is_cancelled=lambda: jobs.is_cancelled(task_result_id),
                                  what="breseq")

**`subprocess.run` cannot do this**: it blocks until the process exits, so there is no moment
at which anything could be asked whether the job is still wanted. The loop is the whole
difference, and it is why cancellation is possible at all -- `django_tasks_db` offers no way to
interrupt a running task, so the task has to interrupt itself.

**The output goes to a file, not a pipe.** `Popen` writes to the log's descriptor directly, so
there is no buffer for this process to drain and nothing to read back: the returncode is all
this returns. It also retires a hazard the pipe version had to guard against in a `finally`
block -- a child blocking on a full pipe nobody is reading.
"""

import logging
import os
import signal
import subprocess
import time

logger = logging.getLogger("mutint_jobs.processes")

#: How often the run loop asks whether somebody has cancelled. One indexed read against a
#: unique column, against a subprocess measured in hours -- the interval is about how long a
#: person waits after pressing the button, not about cost.
CANCEL_POLL_SECONDS = 2

#: Between asking the process group to stop and insisting. breseq traps nothing, so this is
#: only ever the time bowtie2 or samtools take to notice; it is not a shutdown protocol.
KILL_GRACE_SECONDS = 10


class Cancelled(Exception):
    """Raised by `run_tool` when the job was cancelled while the tool was running."""


def run_tool(argv, log, env=None, timeout=None, is_cancelled=None,
             poll_seconds=CANCEL_POLL_SECONDS, what="the command"):
    """Run `argv`, writing everything it prints into `log`. Returns its returncode.

    `log` is a binary file open for appending -- `logs.open_log` yields one -- and receives
    stdout and stderr merged, in the order the tool produced them. A log with no real
    descriptor (`logs._NullLog`, when there is no job) means the output is discarded, which is
    what running outside a job has always done.

    Raises `Cancelled` after stopping the process, or `subprocess.TimeoutExpired`.

    **`start_new_session=True`, and the signal goes to the process group.** This is the part
    that is easy to get wrong and looks correct when it is: breseq spawns bowtie2 and samtools
    as children, so `process.kill()` reaps the parent and leaves them running with no parent at
    all. The job would report itself cancelled while the machine stayed saturated, which is
    worse than not offering the button. A new session makes the whole run one process group
    with one thing to signal.
    """
    target = _descriptor(log)
    _echo(log, argv)

    process = subprocess.Popen(
        argv, stdout=target, stderr=subprocess.STDOUT, env=env, start_new_session=True)

    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            try:
                return process.wait(timeout=poll_seconds)
            except subprocess.TimeoutExpired:
                pass

            if is_cancelled is not None and is_cancelled():
                _stop(process)
                raise Cancelled("%s was cancelled." % what)

            if deadline is not None and time.monotonic() >= deadline:
                _stop(process)
                raise subprocess.TimeoutExpired(argv, timeout)
    finally:
        if process.poll() is None:
            _stop(process)


def _descriptor(log):
    """`log`'s file descriptor for `Popen`, or DEVNULL when it has none.

    The child writes to the descriptor itself, so nothing in this process buffers its output --
    which is what lets the web process tail the file while the tool is still running.
    """
    fileno = getattr(log, "fileno", None)
    if fileno is None:
        return subprocess.DEVNULL
    try:
        return fileno()
    except (OSError, ValueError):
        return subprocess.DEVNULL


def _echo(log, argv):
    """Put the command line in the log, ahead of what it prints.

    A log that opens with the exact argv answers the first question anybody has about a failed
    run -- what was actually run -- without them having to reconstruct it from a form. Flushed
    before the child starts, or this process's buffer would land after the child's output.
    """
    try:
        log.write(("$ " + " ".join(argv) + "\n").encode("utf-8", "replace"))
        log.flush()
    except (OSError, ValueError, AttributeError):
        logger.debug("could not echo a command line into a job log", exc_info=True)


def _stop(process):
    """SIGTERM the whole process group, then SIGKILL what is left."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            process.wait(timeout=KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue

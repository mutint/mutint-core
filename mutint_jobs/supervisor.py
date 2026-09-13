"""A deadman switch for the background worker, and for the database behind it.

`./mutint start` spawns this; this spawns `db_worker`. In between them runs a pipe whose write
end only `start` holds, and which this process blocks on. When `start` dies -- **for any
reason at all, `kill -9` and a hard crash included** -- the kernel closes its copy of the write
end, the read here returns EOF, and this stops the worker and the cluster and exits.

### Why a third process rather than a hook

`start` registers an `atexit` hook, and it covers more than it first appears to: Django's
`run_with_reloader` installs `SIGTERM -> sys.exit(0)`, and its parent branch ends
`except KeyboardInterrupt: pass`, so Ctrl-C, `SIGTERM` and a closed terminal (`SIGHUP`, which
Django does not override, so the entry script's own handler still turns it into `SystemExit`)
all exit the interpreter normally and run it.

What no hook can cover is `SIGKILL`: nothing inside a process acts after it is killed
outright. So the watchdog has to live outside it, and the pipe is the only fully portable way
to notice a parent's death -- `prctl(PR_SET_PDEATHSIG)` is Linux-only and this suite's usual
machine is a Mac.

### The constraints this file is written under

**Standard library only, and nothing from Django.** It spawns a child, waits on a pipe and
sends signals; none of that needs a settings module, and importing one would make this process
fail for reasons that have nothing to do with its job. It is also why it is run **by path**
rather than as `-m mutint_jobs.supervisor`: under an assembled project `mutint_jobs` is
importable only because `config/settings.py` puts the submodule directories on `sys.path`, and
a process that never loads settings has never done that. `mutint_common/pg.py` is loaded by
path here for the same reason it is loaded by path from the entry script, and is usable at all
because it carries the identical constraint.

**One process group holds all of us.** `start` spawns this with `start_new_session=True`, so
Ctrl-C at the terminal never reaches any of us -- `start`'s hook is then the single stopper,
rather than the workers' own SIGINT handlers racing it and holding the database open while the
cluster is being shut down underneath. Each worker is an ordinary child in this group, so
`start`'s `killpg` reaches us all, and on EOF we signal them by pid.

### Why the pool lives here and not in `start`

`--workers N` spawns N of them from this one process, rather than `start` spawning N of these.
The deciding reason is that **the shutdown order below is a global invariant**: workers first,
then the group, then the cluster, because the first two hold database connections. N
supervisors would make it a per-process one -- the first to finish its worker would stop the
cluster while another was still waiting out its own grace period, which is `pg_ctl stop`
underneath a live worker, the exact failure `start`'s two `atexit` hooks are ordered to avoid.
Arming only one of them with `--pg-dir` does not help, because that one cannot see the other
N-1 workers. It also keeps `start`'s side singular: one pipe, one hook, one watcher thread.

**Nothing here ever restarts a worker.** A pool is not supervision: a worker that dies is
announced and not replaced, and the announcement is the whole answer to "a background worker
that dies silently is worse than one you can see" -- which at N workers means a *partial*
death, invisible in every other respect.
"""

import os
import select
import signal
import subprocess
import sys
import time

#: How long the worker gets between SIGTERM and SIGKILL.
#:
#: Deliberately short, and **it must not be "however long the task takes"**: a breseq run is
#: hours, and a Ctrl-C that hangs the terminal for hours is worse than an abandoned task. The
#: honest cost of that choice is that a task in flight is killed and its queue row says
#: RUNNING for ever, because `django_tasks_db` has no reaper for a claimed row -- which is
#: what `./mutint reap_jobs`'s RUNNING window exists to clear up after.
GRACE_SECONDS = 5

#: How often we look at the worker while waiting for the pipe. Only bounds how quickly we
#: notice a worker that exited on its own; the pipe itself is level-triggered and instant.
POLL_SECONDS = 1.0


def _stop_all(processes, grace=GRACE_SECONDS):
    """SIGTERM every live worker, one shared grace period, then SIGKILL the rest.

    **Signal them all before waiting on any of them**, so teardown costs one grace period
    rather than N of them. Serially, four workers that ignore SIGTERM would be forty seconds
    before `_stop_group` and `_stop_cluster` run -- forty seconds of the orphaned runserver
    child holding port 8000, which is the symptom `_stop_group` exists to prevent.

    Never raises: everything below this point runs where nobody is left to read a traceback.
    """
    live = [process for process in processes if process.poll() is None]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not live:
            return
        for process in live:
            try:
                process.send_signal(sig)
            except (OSError, ProcessLookupError):
                pass
        deadline = time.monotonic() + grace
        for process in list(live):
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
                live.remove(process)
            except subprocess.TimeoutExpired:
                pass
            except (OSError, ProcessLookupError):
                live.remove(process)


def _stop(process):
    """One worker, the same way. Kept because a single child is still the common case."""
    _stop_all([process])


def _announce(name, code, remaining):
    """Say that a worker has gone, and how much of the pool is left.

    stderr, which `start` inherits, so this lands in the same terminal as everything else --
    see the note on inherited output in `main`. This is the whole of what replaces restarting
    it: the pool degrades monotonically and says so each time it does.
    """
    sys.stderr.write(
        "\n%s exited (status %s). %s\n"
        % (name,
           code,
           "%d background worker%s still running."
           % (remaining, "" if remaining == 1 else "s") if remaining
           else "No background workers are left; queued work will not run until you restart."))
    sys.stderr.flush()


def _wait_for_parent(read_fd, processes, announce=None):
    """Block until the parent dies or every worker has exited. True if the parent died.

    Two things to wait on and only one of them is a file descriptor, so `select` with a
    timeout and a look at the children in between.

    **One worker exiting is not the end of the wait**, or a single crash would tear down the
    healthy rest of the pool. It is announced instead, once, and the wait carries on until the
    last one has gone -- which is the moment `start`'s watcher thread should print its "queued
    work will not run" sentence, and is exactly what returning False tells it.

    The `poll()` here is also what reaps each child. A `select`-only loop would leave zombies
    behind over a long session.
    """
    # Resolved here rather than as a default argument, which would bind the function at import
    # and make the module attribute unpatchable.
    announce = announce or _announce
    remaining = list(processes)
    while True:
        try:
            ready, _, _ = select.select([read_fd], [], [], POLL_SECONDS)
        except (OSError, InterruptedError):
            return True
        if ready:
            # Readable means either data (nobody writes any) or EOF. Both mean the parent is
            # not coming back, so the distinction is not worth drawing.
            return True
        for name, process in list(remaining):
            code = process.poll()
            if code is None:
                continue
            # Removed before announcing, so a worker is reported once rather than once per
            # second for the rest of the session.
            remaining.remove((name, process))
            announce(name, code, len(remaining))
        if not remaining:
            return False


def _option(argv, name, default=None):
    """Pull `--name value` out of argv. Plain argparse would do, but this file is loaded by
    path into a process with no Django and is kept to the smallest thing that works."""
    if name not in argv:
        return default
    at = argv.index(name)
    value = argv[at + 1]
    del argv[at:at + 2]
    return value


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Optional, and each is independent of the others. `--parent` is what makes the two
    # clean-ups below possible at all; `--group` and `--pg-dir` each say that one more thing is
    # ours to clear up. `start` passes each only when it is safe to -- see its comments.
    parent_pid = _option(argv, "--parent")
    parent_pid = int(parent_pid) if parent_pid else None
    group_pgid = int(_option(argv, "--group", 0))
    pg_base_dir = _option(argv, "--pg-dir")
    # Clamped here as well as in `start`, deliberately: two processes, two defaults, and this
    # one runs where nobody is left to be told that a nonsense value was ignored.
    workers = max(1, int(_option(argv, "--workers", 1)))

    if len(argv) < 2:
        sys.stderr.write("usage: supervisor.py <read-fd> <entry-script> "
                         "[--parent PID] [--group PGID] [--pg-dir DIR] [--workers N]\n")
        return 2

    read_fd = int(argv[0])
    entry_script = argv[1]

    # stdout and stderr are inherited, deliberately. `db_worker.configure_logging` attaches a
    # StreamHandler at INFO when nothing else has, so its start line and one line per task
    # land in the same terminal as runserver's. Capturing them anywhere is precisely what
    # "a background worker that dies silently" means, which is the objection this whole
    # feature had to answer.
    #
    # **Each worker is named**, which matters only because of that inherited output: every line
    # `db_worker` logs carries `worker_id=`, and its default is 32 random characters. With a
    # pool writing into one terminal that is the difference between `mutint-9134-2` and noise.
    # The id also lands in the queue row's `worker_ids`, and so on /jobs/. Qualified with our
    # own pid so two checkouts cannot produce the same names.
    #
    # `--no-startup-delay` is deliberately *not* passed: `db_worker` sleeps a random fraction
    # of a second before its first poll, which its own comment says is there to avoid exactly
    # the thundering herd that starting N of them at once would otherwise be.
    processes = []
    for index in range(workers):
        name = "mutint-%d-%d" % (os.getpid(), index + 1)
        processes.append((name, subprocess.Popen(
            [sys.executable, entry_script, "db_worker", "--no-reload", "--worker-id", name])))

    # **`start` signals this whole group with SIGTERM and then waits for us.** Without a handler
    # we would die instantly on the default action -- so that wait would return at once, its
    # SIGKILL fallback would never fire, and the workers, which answer SIGTERM by *finishing
    # the task in hand* (hours, for a breseq run), would still be holding database connections
    # when the entry script's hook stops the cluster underneath them. So catch it, stop them
    # properly, and only then go.
    def _terminate(_signum, _frame):
        _stop_all([process for _name, process in processes])
        os._exit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    if _wait_for_parent(read_fd, processes) and not _alive_or_unknown(parent_pid):
        # **The order is the whole of the shutdown.** Every worker first, then whatever is left
        # of the parent's group, then the cluster -- because the first two hold database
        # connections, and stopping PostgreSQL underneath a live one is exactly the mistake
        # `start`'s two atexit hooks are ordered to avoid: the connection drops mid-query and
        # `run_task`'s handler tries to record the failure in the database that has just gone.
        # It is also why the pool is N children of one supervisor rather than N supervisors.
        _stop_all([process for _name, process in processes])
        _stop_group(group_pgid)
        _stop_cluster(pg_base_dir, parent_pid)
    return 0


def _alive_or_unknown(pid):
    """True if the parent is somehow still with us, so nothing below should run.

    The pipe having closed is already good evidence that it is not, but the checks after this
    all destroy something, and `None` -- nobody told us which pid to watch -- is the case where
    we cannot know. Refusing then is the safe direction: it leaves the orphans this exists to
    clear up, which is what happens today anyway.
    """
    return pid is None or _alive(pid)


def _stop_group(pgid):
    """Signal what is left of the parent's process group. Never raises.

    This exists for one member: the **runserver child**, which `restart_with_reloader` spawns
    as an ordinary child of `./mutint start` and which a `kill -9` on that process orphans, still
    holding port 8000 -- so the next `./mutint start` fails with "That port is already in use".
    That has always been true and is not this feature's doing, but a deadman that leaves the
    most annoying of the three orphans behind is only half a deadman.

    **The guard is `start`'s, and it is about not killing the wrong thing.** It passes a pgid
    only when it was the group's *leader*, which is exactly what distinguishes "a group
    containing us and our children" -- any interactive shell, which puts a job in its own group
    -- from "the shell's group, which we merely inherited" under `sh -c`. Signalling the latter
    would kill the user's shell. When `start` is not the leader it passes nothing and the
    orphan survives, as it does today. We are in a session of our own, so the signal cannot
    reach us either way.
    """
    if not pgid:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (OSError, ProcessLookupError):
            return
        time.sleep(GRACE_SECONDS if sig is signal.SIGTERM else 0)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop_cluster(base_dir, owner_pid):
    """Stop the cluster our parent owned, if it still owns it. Never raises.

    Loaded by path, because this process has no Django and therefore no `sys.path` entry for
    `mutint_common` under an assembled project. `pg.py` can be used this way at all only
    because it is standard-library-only -- a property it has for the entry script's sake,
    which happens to be exactly what is needed here.
    """
    if not base_dir or owner_pid is None:
        return
    try:
        import importlib.util

        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "mutint_common", "pg.py")
        spec = importlib.util.spec_from_file_location("mutint_pg_for_supervisor", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.Cluster(base_dir).stop_if_owner_is(owner_pid)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())

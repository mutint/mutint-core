"""A deadman switch for the background worker, and for the database behind it.

`./aledb start` spawns this; this spawns `db_worker`. In between them runs a pipe whose write
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
rather than as `-m aledb_jobs.supervisor`: under an assembled project `aledb_jobs` is
importable only because `config/settings.py` puts the submodule directories on `sys.path`, and
a process that never loads settings has never done that. `aledb_common/pg.py` is loaded by
path here for the same reason it is loaded by path from the entry script, and is usable at all
because it carries the identical constraint.

**One process group holds both of us.** `start` spawns this with `start_new_session=True`, so
Ctrl-C at the terminal never reaches either of us -- `start`'s hook is then the single
stopper, rather than the worker's own SIGINT handler racing it and holding the database open
while the cluster is being shut down underneath. The worker is an ordinary child in this
group, so `start`'s `killpg` reaches us both, and on EOF we signal it by pid.
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
#: what `./aledb reap_jobs`'s RUNNING window exists to clear up after.
GRACE_SECONDS = 5

#: How often we look at the worker while waiting for the pipe. Only bounds how quickly we
#: notice a worker that exited on its own; the pipe itself is level-triggered and instant.
POLL_SECONDS = 1.0


def _stop(process):
    """SIGTERM, a grace period, then SIGKILL. Never raises."""
    if process.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            process.send_signal(sig)
        except (OSError, ProcessLookupError):
            return
        try:
            process.wait(timeout=GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _wait_for_parent(read_fd, process):
    """Block until the parent dies or the worker exits. True if the parent died.

    Two things to wait on and only one of them is a file descriptor, so `select` with a
    timeout and a look at the child in between. A worker that exits on its own must not leave
    this process blocked on the pipe for ever: `start` watches exactly one child -- us -- so
    our exit is what tells it the worker is gone.
    """
    while True:
        try:
            ready, _, _ = select.select([read_fd], [], [], POLL_SECONDS)
        except (OSError, InterruptedError):
            return True
        if ready:
            # Readable means either data (nobody writes any) or EOF. Both mean the parent is
            # not coming back, so the distinction is not worth drawing.
            return True
        if process.poll() is not None:
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

    if len(argv) < 2:
        sys.stderr.write("usage: supervisor.py <read-fd> <entry-script> "
                         "[--parent PID] [--group PGID] [--pg-dir DIR]\n")
        return 2

    read_fd = int(argv[0])
    entry_script = argv[1]

    # stdout and stderr are inherited, deliberately. `db_worker.configure_logging` attaches a
    # StreamHandler at INFO when nothing else has, so its start line and one line per task
    # land in the same terminal as runserver's. Capturing them anywhere is precisely what
    # "a background worker that dies silently" means, which is the objection this whole
    # feature had to answer.
    process = subprocess.Popen([sys.executable, entry_script, "db_worker", "--no-reload"])

    if _wait_for_parent(read_fd, process) and not _alive_or_unknown(parent_pid):
        # **The order is the whole of the shutdown.** The worker first, then whatever is left
        # of the parent's group, then the cluster -- because the first two hold database
        # connections, and stopping PostgreSQL underneath a live one is exactly the mistake
        # `start`'s two atexit hooks are ordered to avoid: the connection drops mid-query and
        # `run_task`'s handler tries to record the failure in the database that has just gone.
        _stop(process)
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
    as an ordinary child of `./aledb start` and which a `kill -9` on that process orphans, still
    holding port 8000 -- so the next `./aledb start` fails with "That port is already in use".
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
    `aledb_common` under an assembled project. `pg.py` can be used this way at all only
    because it is standard-library-only -- a property it has for the entry script's sake,
    which happens to be exactly what is needed here.
    """
    if not base_dir or owner_pid is None:
        return
    try:
        import importlib.util

        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "aledb_common", "pg.py")
        spec = importlib.util.spec_from_file_location("aledb_pg_for_supervisor", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.Cluster(base_dir).stop_if_owner_is(owner_pid)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())

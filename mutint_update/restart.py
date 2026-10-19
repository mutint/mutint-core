"""Stopping this installation, and starting it again, from inside it.

`/update/` stages a version and the next launch applies it, so the last step of an update
has always been an instruction somebody has to follow: quit MutInt, start it again. This is
that step as a button, and it works wherever `./mutint start` is what is running -- a
terminal on WSL or Linux, `MutInt.app`, a systemd unit -- because the process that command
becomes restarts **itself, in place**.

**The process to restart is `./mutint start`'s own, and it can exec over itself.** The entry
script `os.execv`s into the venv's Python and keeps its pid, and under `runserver` that
process is the reloader's *parent*: it owns the PostgreSQL cluster (`env/pg-owner` names its
pid), it holds the terminal, and it registered the shutdown hooks. Views run in the
reloader's child, which Django marks with `RUN_MAIN` and restarts whenever it dies -- so a
view cannot stop MutInt by stopping itself (`server_pid` is the whole of telling the two
apart). What the view does instead is signal the parent, and the parent exits exactly the
way Ctrl-C exits it -- `SystemExit` out of Django's reloader loop, whose `subprocess.run`
kills the child on the way, then `atexit` -- except that the last `atexit` hook, when a
restart was asked for, `execve`s a fresh `./mutint start` over the same pid.

Three things fall out of exec rather than exit-and-relaunch, and each is why this is the
design rather than a launcher that knows how to start MutInt again:

- **Same pid, so the cluster is never stopped.** `atexit` is LIFO: the entry script
  registers the cluster's `stop_if_owned` first, `arm()` registers the exec hook after it,
  and `start.py` registers the worker hook last -- so on exit the workers stop, then this
  hook execs and the cluster hook is never reached. The new image's `_ensure_database()`
  finds the cluster running with a live owner, itself, and leaves it alone. That is also
  what makes the pre-update `pg_dump` real: `update.backup` dumps only from a running
  cluster, and a restart that stopped it first skipped the backup on every staged update.
  **`arm()` therefore has to run before the worker is spawned**, and `start.py` does.
- **Same terminal.** The relaunched server writes to the window `./mutint start` was typed
  in and Ctrl-C still stops it, which is the whole of what a WSL or Linux user needs and
  what a detached helper could not give them. Under `MutInt.app` the pid the applet
  captured is still the server's, so its Quit and its deadman keep working.
- **No `RUN_MAIN`.** Django sets it in the child only, so the parent's environment is a
  first launch's: the new start migrates, spawns workers and prints its banner with no
  `unset` anywhere. (The detached helper this replaced had to strip it, because view code
  is always the child and `open` handed the child's environment on.)

**What launched MutInt no longer matters, and nothing exports anything.** `arm()` marks the
environment so the child can tell the page a restart is possible; the one case left without
a button is a copy not started through `./mutint start` at all -- a bare `runserver`, or a
WSGI server -- which can only be stopped and started from where it was started, and the
endpoint says so with a 409.

**The signal is sent after a pause**, because the response to the request asking for the
restart has to reach the browser before the process serving it is killed.
"""

import atexit
import os
import signal
import sys
import threading

#: Set in the environment by `arm()`, in the process that will exec itself, and inherited by
#: the reloader's child -- so a view can answer "can this MutInt restart itself". Absent means
#: it cannot, which is a state the page renders rather than an error it raises.
ARMED_ENV = 'MUTINT_RESTART_ARMED'

#: Django sets this in the reloader's *child* and nowhere else -- the same fact
#: `start.py`'s `is_first_launch` reads, spelled here because importing a management
#: command from a view to get at one string is worse than repeating it.
RELOADER_CHILD_ENV = 'RUN_MAIN'

#: What the child sends its parent. Nothing else in the process uses it: Django's reloader
#: takes SIGTERM, the entry script SIGTERM and SIGHUP.
RESTART_SIGNAL = signal.SIGUSR1

#: Long enough for the response to this request to be written and read. It is not a race in
#: any meaningful sense -- the browser is on the same machine -- but the cost of being wrong
#: is a page that reports a failure while the restart it asked for succeeds behind it.
RESPONSE_GRACE_SECONDS = 2

#: Exported by the entry script before it execs, and re-exported by the one it execs into:
#: dropped from the relaunch's environment so the new launch derives them afresh rather
#: than inheriting a stale answer.
_ENTRY_SCRIPT_EXPORTS = ('MUTINT_DB_HOST', 'MUTINT_DB_PORT', 'MUTINT_DB_NAME',
                         'MUTINT_DB_USER', 'MUTINT_DB_MANAGED')

_requested = False
_relaunch = None


def armed():
    """Whether this MutInt can start itself again -- whether `arm()` ran in the process that
    would do it."""
    return os.environ.get(ARMED_ENV) == '1'


def server_pid():
    """The process whose exit stops MutInt.

    The reloader's child serves requests and is restarted whenever it dies, so it is never
    the answer; its parent is. Without the reloader there is no child and we are it.
    """
    if os.environ.get(RELOADER_CHILD_ENV) == 'true':
        return os.getppid()
    return os.getpid()


def relaunch_argv(argv=None, project_root=None):
    """The command that starts MutInt again: the managed interpreter where the entry script
    has provisioned one, else the entry script itself through its shebang, with the same
    arguments (`--workers N`, `--no-worker`) this launch was given.

    `env/python/bin/python3` first because it is what the entry script would choose for
    itself, and exists after any first run; the host `python3` the shebang names may be a
    stub (a Mac with no developer tools) or may have changed since this process started.
    """
    argv = list(sys.argv if argv is None else argv)
    script = os.path.abspath(argv[0])
    if project_root is None:
        project_root = _project_root(script)
    managed = os.path.join(project_root, 'env', 'python', 'bin', 'python3')
    if os.path.isfile(managed) and os.access(managed, os.X_OK):
        return [managed, script] + argv[1:]
    return [script] + argv[1:]


def _project_root(script):
    try:
        from mutint_common import update
        root = update.project_root()
    except Exception:
        root = None
    return root or os.path.dirname(script)


def arm():
    """Make this process able to restart itself: call once, in the process that
    `./mutint start` became, **before the worker is spawned** (see the module docstring for
    why the order is load-bearing).

    Installs the signal handler that turns `RESTART_SIGNAL` into the exit Ctrl-C produces,
    registers the `atexit` hook that execs a fresh start when that signal was the reason for
    exiting, and marks the environment so the reloader's child -- which inherits it -- can
    offer the button.
    """
    global _relaunch
    _relaunch = relaunch_argv()
    signal.signal(RESTART_SIGNAL, _on_restart_signal)
    atexit.register(_relaunch_if_requested)
    os.environ[ARMED_ENV] = '1'
    return _relaunch


def _on_restart_signal(signum, frame):
    """Exit the way Ctrl-C does, remembering why. Raised inside Django's reloader loop this
    unwinds `subprocess.run`, which kills the serving child on its way out; without the
    reloader it unwinds the server itself. Either way `atexit` runs next."""
    global _requested
    _requested = True
    raise SystemExit(0)


def _relaunch_if_requested():
    """The last `atexit` hook that matters: exec `./mutint start` over this process.

    Only when the signal asked for it -- on Ctrl-C and every other exit this returns and the
    cluster's own hook runs after it as before. `execve` never returns on success; on failure
    the reason is printed and the process finishes exiting as a plain stop, which leaves the
    staged update to the next `./mutint start` by hand.
    """
    if not _requested or not _relaunch:
        return
    env = {key: value for key, value in os.environ.items()
           if key != RELOADER_CHILD_ENV and key not in _ENTRY_SCRIPT_EXPORTS}
    env.pop(ARMED_ENV, None)
    sys.stdout.write('\nRestarting MutInt...\n')
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execve(_relaunch[0], _relaunch, env)
    except OSError as error:
        sys.stderr.write('Could not start MutInt again (%s): run `%s` yourself.\n'
                         % (error, ' '.join(_relaunch)))
        sys.stderr.flush()


def _note_restart():
    """Tell the next launch not to open a browser -- see `update.note_restart`. Best effort:
    a restart that works and opens an extra window beats one that refuses over a state file."""
    try:
        from mutint_common import update
        root = update.project_root()
        # No root means nothing told us which installation this is, and `.` would write a
        # state file wherever the process happens to be standing.
        if root:
            update.note_restart(root)
    except Exception:
        pass


def _signal_later(pid):
    """Send the restart signal after the response has had time to leave. Separated so tests
    can watch it without one being sent."""
    timer = threading.Timer(RESPONSE_GRACE_SECONDS, os.kill, (pid, RESTART_SIGNAL))
    timer.daemon = True
    timer.start()


def request_restart():
    """Stop MutInt and start it again. Returns the pid that will be signalled.

    Returns rather than waits: by the time anything could be observed here, the process
    observing it is gone.
    """
    if not armed():
        raise RuntimeError("This MutInt was not started by `./mutint start`, "
                           "so it cannot start itself again.")
    pid = server_pid()
    # Before signalling, so it is on disk whatever happens next. A state file rather than an
    # environment variable because the launch that reads it is a different image of this
    # process, and a relaunch that fails or is retried still finds the note to take once.
    _note_restart()
    _signal_later(pid)
    return pid

"""Stopping this installation, and starting it again, from inside it.

`/upgrade/` stages a version and the next launch applies it, so the last step of an upgrade
has always been an instruction somebody has to follow: quit MutInt, start it again. This is
that step as a button.

**Nothing here knows what launched MutInt.** The launcher says how to start it again by
exporting `MUTINT_RELAUNCH_COMMAND`, and when nothing exported one there is no button --
which is the honest answer from a terminal, where the process that would have to run the
command is the one being killed. MutInt.app sets it to an `open` of its own bundle; a
deployment under systemd could set `systemctl restart mutint` and get the same page. That is
why the seam is an environment variable rather than anything about macOS: core has no
business knowing which of those it is.

**A view cannot stop MutInt by stopping itself.** Under `runserver` the reloader parent and
the child that serves requests are two processes, and view code runs in the child -- so a
view that exits, or kills its own pid, is simply restarted by the reloader a moment later.
What has to be signalled is the parent, and `server_pid` is the whole of telling them apart:
Django sets `RUN_MAIN` in the child and nowhere else. With the reloader off -- `DEBUG` false,
which is what an installed deployment runs -- there is only one process and it is us.

**And the work is done by a detached helper, not here.** Two reasons, and neither is
squeamishness: the response has to reach the browser before the process serving it dies, and
whatever relaunches MutInt has to outlive everything this one is about to kill. The helper
gets its own session (`start_new_session`), which is what puts it outside the process group
the deadman supervisor tears down -- the same group membership that makes `kill -9` on the
app clean up the runserver child.
"""

import os
import subprocess

#: What the launcher exports to say it can start MutInt again. Absent means it cannot, which
#: is a state the page renders rather than an error it raises.
RELAUNCH_ENV = 'MUTINT_RELAUNCH_COMMAND'

#: Django sets this in the reloader's *child* and nowhere else -- the same fact
#: `start.py`'s `is_first_launch` reads, spelled here because importing a management
#: command from a view to get at one string is worse than repeating it.
RELOADER_CHILD_ENV = 'RUN_MAIN'

#: Long enough for the response to this request to be written and read. It is not a race in
#: any meaningful sense -- the browser is on the same machine -- but the cost of being wrong
#: is a page that reports a failure while the restart it asked for succeeds behind it.
RESPONSE_GRACE_SECONDS = 2

#: After the server's process is gone, before starting one again. The listening socket is
#: released when the process exits and `runserver` sets SO_REUSEADDR, so this is not about
#: the port; it is about the deadman supervisor, which is stopping the worker and the
#: PostgreSQL cluster in that window and whose work the next launch would otherwise race.
SETTLE_SECONDS = 3

#: How long to wait for a polite exit before insisting. Generous: what it is waiting for is
#: `atexit` stopping a database cluster.
STOP_TIMEOUT_SECONDS = 60


def relaunch_command():
    """The command that starts MutInt again, or None if nothing said how."""
    return (os.environ.get(RELAUNCH_ENV) or '').strip() or None


def server_pid():
    """The process whose exit stops MutInt.

    The reloader's child serves requests and is restarted whenever it dies, so it is never
    the answer; its parent is. Without the reloader there is no child and we are it.
    """
    if os.environ.get(RELOADER_CHILD_ENV) == 'true':
        return os.getppid()
    return os.getpid()


def _script(pid, command):
    """The helper, as shell.

    `kill -0` is the poll: it tests that the pid can be signalled without signalling it. The
    loop is bounded so a process that will not die cannot leave the helper running for ever,
    and past the bound it stops being polite -- an upgrade that half-happened because
    something ignored SIGTERM is worse than an ungraceful stop.
    """
    return "\n".join([
        "sleep %d" % RESPONSE_GRACE_SECONDS,
        "kill -TERM %d 2>/dev/null" % pid,
        "waited=0",
        "while kill -0 %d 2>/dev/null && [ $waited -lt %d ]; do" % (
            pid, STOP_TIMEOUT_SECONDS * 2),
        "    sleep 0.5",
        "    waited=$((waited + 1))",
        "done",
        "kill -0 %d 2>/dev/null && kill -KILL %d 2>/dev/null" % (pid, pid),
        "sleep %d" % SETTLE_SECONDS,
        command,
    ])


def _note_restart():
    """Tell the next launch not to open a browser -- see `upgrade.note_restart`. Best effort:
    a restart that works and opens an extra window beats one that refuses over a state file."""
    try:
        from mutint_common import upgrade
        root = upgrade.project_root()
        # No root means nothing told us which installation this is, and `.` would write a
        # state file wherever the process happens to be standing.
        if root:
            upgrade.note_restart(root)
    except Exception:
        pass


def _spawn(script):
    """Run the helper, detached. Separated so tests can watch it without one running."""
    subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def request_restart():
    """Stop MutInt and start it again. Returns the pid that will be signalled.

    Returns rather than waits: by the time anything could be observed here, the process
    observing it is gone.
    """
    command = relaunch_command()
    if not command:
        raise RuntimeError("Nothing told MutInt how to start itself again.")
    pid = server_pid()
    # Before spawning, so it is on disk whatever happens next: the launch this note is for is
    # started by a detached helper, and an environment variable could not reach it.
    _note_restart()
    _spawn(_script(pid, command))
    return pid

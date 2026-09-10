import atexit
import os
import signal
import subprocess
import sys
import threading

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command

# Django's autoreloader does not swap code into the running process -- it
# re-executes the whole command line in a loop (see
# django.utils.autoreload.restart_with_reloader), respawning the child every time
# a watched file changes. So everything in handle() runs again on every reload.
#
# Ungated, that meant each touched file re-ran `migrate --run-syncdb` and popped a
# fresh browser window, which reads as the server rebooting rather than reloading.
# RUN_MAIN is set only in the reloader's child, so its absence marks the launch.
RELOADER_CHILD_ENV = 'RUN_MAIN'

#: How long the worker's process group gets between SIGTERM and SIGKILL at shutdown.
#: Short deliberately -- see mutint_jobs/supervisor.py's GRACE_SECONDS, which owns the reason.
WORKER_STOP_GRACE_SECONDS = 5


def is_first_launch():
    return os.environ.get(RELOADER_CHILD_ENV) != 'true'


def _entry_script():
    """The `./mutint` (or `./mutint`) script that invoked us, absolutely."""
    return os.path.abspath(sys.argv[0])


def _supervisor_script():
    from mutint_jobs import supervisor
    return os.path.abspath(supervisor.__file__)


def _spawn_worker():
    """Start a background worker under a deadman supervisor. Returns (process, write_fd).

    Answers None when a worker should not be started, and says why -- see the gates below.

    **The whole point is that the queue actually drains.** Before this, `./mutint start` printed
    a line asking you to run `db_worker` in another terminal, and the observable result was
    four coverage tasks sitting at READY in two dev databases with nothing that would ever
    clear them. A line printed once at startup is not a thing anybody reads on the day it
    matters.

    The refusal this replaces named two real hazards and was right about both:

    - *"runserver re-executes this whole command line on every code change, so a spawned one
      becomes an orphan-management problem."* True, and `is_first_launch()` -- the RUN_MAIN
      gate this module already had for `migrate` -- is the answer. We are the reloader's
      parent; it re-executes only the child, so nothing here runs twice.
    - *"a background worker that dies silently is worse than one you can see."* Also true, and
      not answered by process management at all. Three things answer it: the child's output is
      inherited rather than captured, `_watch_worker` says so when it exits, and `/jobs/` says
      so when work has been waiting with nobody taking it.
    """
    read_fd, write_fd = os.pipe()
    argv = [sys.executable, _supervisor_script(), str(read_fd), _entry_script()]

    # Our own process group, so the supervisor can also clear up the *runserver child* if we
    # are killed outright. `restart_with_reloader` runs it with `subprocess.run`, so it is an
    # ordinary child in this group -- and `kill -9` here orphans it holding port 8000, which
    # is what makes the next `./mutint start` fail with "That port is already in use". That has
    # always been true and is not this feature's doing, but a deadman that leaves the most
    # annoying orphan of the three behind is only half a deadman.
    #
    # **Passed only when we are the group leader**, and the supervisor checks it again. With
    # job control -- any interactive shell -- the shell puts `./mutint start` in a group of its
    # own, so the group is exactly us and our children. Without it (a script, `sh -c`) we
    # inherit the shell's group, and signalling that would kill the shell. Leadership is the
    # precise test for the difference; when we are not the leader the supervisor is told
    # nothing and the orphan survives, exactly as it does today.
    argv += ["--parent", str(os.getpid())]
    if os.getpgrp() == os.getpid():
        argv += ["--group", str(os.getpgrp())]

    # The cluster half of the deadman, armed only when we own the cluster. We can answer that
    # directly: `os.execv` preserved the pid, so this process *is* the owner the entry script
    # recorded. An unowned cluster -- `./mutint db start`, which exists so a server outlives one
    # command -- must be left alone, so we pass nothing and the supervisor touches nothing.
    if os.environ.get('MUTINT_DB_MANAGED') == '1':
        base_dir = os.path.dirname(_entry_script())
        try:
            from mutint_common import pg
            if pg.Cluster(base_dir).owned_by_me():
                argv += ["--pg-dir", base_dir]
        except Exception:
            pass

    # `--no-reload` explicitly, because its default is `settings.DEBUG` and this is a dev
    # server. `db_worker`'s own help says why not: "tasks may not be stopped cleanly". Django's
    # reloader exits from the main thread while the worker loop is a daemon thread, so a saved
    # file guillotines the task in flight -- for a breseq run that leaves a stranded row *and*
    # an orphaned breseq process group nothing will ever signal, because runner.py deliberately
    # put it in a session of its own. The cost of not reloading is that the worker runs the
    # code as of launch, which the banner says out loud.
    #
    # `start_new_session` keeps the terminal's Ctrl-C away from it, so our shutdown hook is the
    # single stopper rather than racing db_worker's own SIGINT handler -- which would otherwise
    # be shutting down gracefully, holding the database open, while the cluster is stopped
    # underneath it.
    #
    # stdout and stderr are deliberately NOT passed: inherited, so the worker's log lines land
    # in this terminal. Capturing them is exactly what "dies silently" means.
    process = subprocess.Popen(argv, pass_fds=(read_fd,), start_new_session=True)
    os.close(read_fd)
    return process, write_fd


def _stop_worker(process, stopping):
    """Signal the supervisor's whole process group. Silent, and never raises.

    `killpg`, not `process.kill()`: the supervisor has the worker as a child in its group, and
    signalling only the leader leaves a worker running with no parent -- the orphan the old
    refusal was worried about. Same reasoning as mutint-breseq's runner.py.
    """
    stopping.set()
    try:
        if process.poll() is not None:
            return
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        try:
            process.wait(timeout=WORKER_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _watch_worker(process, stopping, write):
    """Say so, loudly, if the worker exits on its own.

    Without this a worker that dies three seconds in leaves a terminal that looks entirely
    normal for the next six hours -- which is the state this feature exists to fix,
    reintroduced one level down. The `stopping` flag is what keeps it quiet when *we* are the
    ones ending it, or every Ctrl-C would print it.
    """
    code = process.wait()
    if stopping.is_set():
        return
    write("\nThe background worker exited (status %s). Queued work will not run until you\n"
          "restart, or run `%s db_worker` in another terminal.\n"
          % (code, os.path.basename(_entry_script())))


def _asked_for_this_restart():
    """Whether /upgrade/'s Restart button is what started this launch.

    Cannot raise: an installation whose `data/` is unreadable should still start, and the cost
    of answering no is a browser window somebody did not need.
    """
    try:
        from mutint_common import upgrade
        root = upgrade.project_root()
        # No root means nothing exported one, which under the entry script cannot happen --
        # and guessing `dirname(sys.argv[0])` would read, and clear, the state file of whatever
        # checkout the process happens to be standing in. Under the test runner that is this
        # repository's own.
        if not root:
            return False
        return upgrade.take_restart_note(root)
    except Exception:
        return False


class Command(BaseCommand):
    help = 'Run migrations, create default admin user, and start the development server'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-worker', action='store_true',
            help='Do not start a background worker alongside the server.')

    def handle(self, *args, **options):
        url = 'http://127.0.0.1:8000'

        if is_first_launch():
            # Before the worker, and not merely tidier: `db_worker`'s loop opens with
            # `DBTaskResult.objects.ready()`, and on a fresh checkout that table does not exist
            # until this runs. A missing relation raises ProgrammingError, which the worker
            # loop does not catch -- it catches OperationalError only -- so a worker started
            # first dies in its first second, on precisely the clone-and-run path this suite is
            # built around. It is also the reason the spawn lives here rather than in the entry
            # script, which has no `migrate` to be after.
            # Before migrating, never after: a database that remembers migrations this
            # checkout no longer ships cannot be migrated forward, and `migrate` would
            # discover that several steps later as a relation-already-exists error naming
            # nothing that explains it. Nobody is standing by when this runs, which is
            # exactly why it has to say so itself. See mutint_common/migration_guard.py.
            from django.db import connection

            from mutint_common import migration_guard
            refusal = migration_guard.check(connection)
            if refusal:
                raise CommandError(refusal)

            call_command('migrate', '--run-syncdb')

            from django.contrib.auth.models import User
            if not User.objects.filter(username='admin').exists():
                User.objects.create_superuser('admin', 'admin@example.com', 'admin')
                self.stdout.write('Created superuser: admin / admin')
            else:
                self.stdout.write('Superuser already exists')

            worker = self._start_worker(options.get('no_worker'))

            # **Not on a restart asked for from the page.** Opening a browser is right for
            # somebody who double-clicked an icon, and wrong here: they are already looking at
            # MutInt in a window that is polling for the server to come back, and `open`
            # navigates it to the site root instead -- so pressing Restart on /upgrade/ took
            # them away from the page that said it would reload. See `upgrade.note_restart`.
            if not _asked_for_this_restart():
                threading.Timer(1.0, lambda: subprocess.run(
                    ['open', url], capture_output=True
                )).start()

            self.stdout.write(f'\nStarting MutInt at {url}')
            self.stdout.write(f'  Admin interface: {url}/admin/  (login: admin / admin)')
            if worker is not None:
                self.stdout.write(
                    '  Background worker running (pid %d): coverage and other queued work is\n'
                    '  derived as it arrives. It runs the code as of now -- restart to pick up\n'
                    '  changes to a task. Start without one with --no-worker.\n' % worker.pid)
            else:
                # Kept for the case where we did not start one. The symptom of not knowing is a
                # sample that imports perfectly and quietly has no coverage track.
                self.stdout.write(
                    '  No background worker. Queued work will not run until you start one:\n'
                    '  `./mutint db_worker` in another terminal, or `./mutint coverage`.\n')

        call_command('runserver')

    def _start_worker(self, no_worker):
        """Spawn the worker if we should, and arrange for it to die with us. Returns it, or None.

        Three gates, and each refuses quietly with a sentence rather than failing the command:
        a dev server that will not start because a worker could not is the wrong trade.
        """
        if no_worker:
            return None
        # Asked through mutint_jobs, which is already the one place in mutint-core that knows
        # which backend is configured. Under ImmediateBackend there is no queue to drain, and
        # `db_worker` would fail its own `valid_backend_name` and die with an argparse error on
        # every launch.
        from mutint_jobs import queue
        if not queue.uses_database_backend():
            self.stdout.write(
                '  Not starting a background worker: TASKS is not the database backend.')
            return None

        try:
            process, _write_fd = _spawn_worker()
        except OSError as error:
            self.stdout.write('  Could not start a background worker: %s' % error)
            return None

        # `_write_fd` is deliberately never closed and never stored anywhere that could close
        # it: it is the deadman. Its only copy lives in this process, so the kernel closing it
        # when we die -- however we die, `kill -9` included -- is what tells the supervisor to
        # stop the worker. See mutint_jobs/supervisor.py.
        stopping = threading.Event()

        # Registered here, which puts it *after* the entry script's cluster hook and therefore
        # -- atexit being LIFO -- makes it run *first*. That order is load-bearing: stopping
        # PostgreSQL under a live worker drops its connection mid-task, and `run_task`'s
        # handler then tries to record the failure in the database that has just gone away, so
        # every Ctrl-C would end in a traceback with no apparent cause. It falls out of where
        # the two are registered, so a reorganisation that moves either one breaks it silently.
        atexit.register(_stop_worker, process, stopping)
        threading.Thread(target=_watch_worker, args=(process, stopping, self.stdout.write),
                         daemon=True).start()
        return process

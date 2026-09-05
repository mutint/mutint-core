"""`./mutint start` under the autoreloader.

Django's autoreloader does not hot-swap code -- restart_with_reloader() re-runs
the entire command line in a `while True` loop, respawning the child on every
watched-file change. So `handle()` executes again on each reload, and anything
in it that is not idempotent-and-silent happens again too.

That is what made a code edit look like a reboot: every reload re-ran
`migrate --run-syncdb` and fired the browser-open timer, so a window popped each
time. The launch-only work is now gated on RUN_MAIN, which Django sets only in
the reloader's child.
"""

import os
import signal
import sys
import threading
import unittest
from io import StringIO
from unittest import mock

from django.test import TestCase, override_settings

from mutint_common.management.commands import start


class IsFirstLaunchTestCase(unittest.TestCase):

    def test_launch_process_has_no_run_main(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(start.is_first_launch())

    def test_the_reloader_child_is_not_a_launch(self):
        with mock.patch.dict(os.environ, {"RUN_MAIN": "true"}):
            self.assertFalse(start.is_first_launch())

    def test_noreload_still_counts_as_a_launch(self):
        """With --noreload there is no child at all, so the work must still run."""
        with mock.patch.dict(os.environ, {"RUN_MAIN": "false"}):
            self.assertTrue(start.is_first_launch())


class StartCommandTestCase(TestCase):

    def _run(self, env):
        command = start.Command()
        command.stdout = StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(start, "call_command") as call, \
                mock.patch.object(start.threading, "Timer") as timer:
            command.handle()
        return [c.args[0] for c in call.call_args_list], timer.call_count

    def test_launch_migrates_and_opens_a_browser(self):
        commands, timers = self._run({})
        self.assertIn("migrate", commands)
        self.assertEqual(1, timers)

    def test_a_reload_does_neither(self):
        commands, timers = self._run({"RUN_MAIN": "true"})
        self.assertNotIn("migrate", commands)
        self.assertEqual(0, timers, "a reload must not pop another browser window")

    def test_a_reload_still_serves(self):
        """The gate must not take the server with it."""
        commands, _timers = self._run({"RUN_MAIN": "true"})
        self.assertEqual(["runserver"], commands)

    def test_the_launch_banner_is_not_repeated(self):
        command = start.Command()
        command.stdout = StringIO()
        with mock.patch.dict(os.environ, {"RUN_MAIN": "true"}, clear=True), \
                mock.patch.object(start, "call_command"), \
                mock.patch.object(start.threading, "Timer"):
            command.handle()
        self.assertEqual("", command.stdout.getvalue())


@override_settings(TASKS={"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}})
class WorkerSpawnTestCase(TestCase):
    """`./mutint start` starts a background worker, and only when it should.

    It did not use to, and the observable result was coverage tasks sitting at READY in two dev
    databases with nothing in existence that would ever run them: `prune_db_task_results` only
    touches *finished* rows, so an unclaimed one is immortal.

    **The suite forces `ImmediateBackend`**, so every test here that expects a spawn has to
    override `TASKS` back -- otherwise it silently asserts the refusal path instead. That is
    the trap `mutint_import/tests/test_tasks.py` already documents, and it is why the
    no-database-backend case below is a test of its own rather than an assumption.
    """

    def _run(self, env, **options):
        command = start.Command()
        command.stdout = StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(start, "call_command") as call, \
                mock.patch.object(start.threading, "Timer"), \
                mock.patch.object(start.threading, "Thread"), \
                mock.patch.object(start, "atexit") as at_exit, \
                mock.patch.object(start.subprocess, "Popen") as popen:
            command.handle(**options)
        return popen, call, at_exit, command.stdout.getvalue()

    # --- the gate ------------------------------------------------------------

    def test_a_launch_starts_exactly_one(self):
        popen, _call, _at_exit, out = self._run({})
        self.assertEqual(1, popen.call_count)
        self.assertIn("Background worker running", out)

    def test_a_reload_starts_none(self):
        """Ungated, every saved file would start another and nothing would stop them."""
        popen, _call, _at_exit, _out = self._run({"RUN_MAIN": "true"})
        self.assertEqual(0, popen.call_count)

    def test_noreload_still_starts_one(self):
        """With --noreload there is no child, so this process is the whole server."""
        popen, _call, _at_exit, _out = self._run({"RUN_MAIN": "false"})
        self.assertEqual(1, popen.call_count)

    def test_no_worker_starts_none_and_says_how_to_run_one(self):
        popen, _call, _at_exit, out = self._run({}, no_worker=True)
        self.assertEqual(0, popen.call_count)
        self.assertIn("db_worker", out)

    @override_settings(TASKS={"default": {"BACKEND": "django.tasks.backends.immediate."
                                                     "ImmediateBackend"}})
    def test_another_backend_starts_none_and_says_so(self):
        """`db_worker` would fail its own `valid_backend_name` and die on every launch."""
        popen, _call, _at_exit, out = self._run({})
        self.assertEqual(0, popen.call_count)
        self.assertIn("not the database backend", out)

    # --- what is actually spawned --------------------------------------------

    def test_it_spawns_the_supervisor_and_not_the_worker_directly(self):
        popen, _call, _at_exit, _out = self._run({})
        argv = popen.call_args.args[0]

        self.assertIs(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("supervisor.py"), argv[1])
        self.assertEqual(int(argv[2]), popen.call_args.kwargs["pass_fds"][0])

    def test_it_is_told_the_pid_to_watch(self):
        """Everything the supervisor destroys is gated on this parent actually being gone."""
        argv = self._run({})[0].call_args.args[0]
        self.assertIn("--parent", argv)
        self.assertEqual(str(os.getpid()), argv[argv.index("--parent") + 1])

    def test_the_group_is_passed_only_when_we_lead_it(self):
        """Otherwise the group is the shell's, inherited under `sh -c`, and signalling it would
        kill the shell. Leadership is the precise test for the difference."""
        argv = self._run({})[0].call_args.args[0]
        led = os.getpgrp() == os.getpid()
        self.assertEqual(led, "--group" in argv)

    def test_the_child_gets_its_own_session(self):
        """Or Ctrl-C reaches the worker too, and db_worker's own graceful shutdown races our
        hook -- holding the database open while the cluster is stopped underneath it."""
        popen, _call, _at_exit, _out = self._run({})
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_its_output_is_not_captured(self):
        """The cheapest half of "a worker that dies silently is worse than one you can see",
        and exactly the sort of thing a later tidy-up redirects to a log file."""
        kwargs = self._run({})[0].call_args.kwargs
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)

    def test_migrating_comes_before_spawning(self):
        """Not tidiness: `db_worker`'s loop opens with `DBTaskResult.objects.ready()`, and on a
        fresh checkout that table does not exist until `migrate --run-syncdb` has run. The
        missing relation raises ProgrammingError, which the worker loop does not catch, so a
        worker started first dies in its first second. It is also the reason this lives in
        start.py rather than in the entry script, which has no migrate to be after."""
        parent = mock.Mock()
        command = start.Command()
        command.stdout = StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(start.threading, "Timer"), \
                mock.patch.object(start.threading, "Thread"), \
                mock.patch.object(start, "atexit"):
            parent.attach_mock(mock.patch.object(start, "call_command").start(), "call")
            parent.attach_mock(mock.patch.object(start.subprocess, "Popen").start(), "popen")
            self.addCleanup(mock.patch.stopall)
            command.handle()

        names = [name for name, args, _kwargs in parent.mock_calls
                 if name in ("call", "popen")]
        self.assertEqual(["call", "popen"], names[:2])
        self.assertEqual("migrate", parent.mock_calls[0].args[0])

    def test_the_shutdown_hook_is_registered_on_a_launch_only(self):
        self.assertEqual(1, self._run({})[2].register.call_count)
        self.assertEqual(0, self._run({"RUN_MAIN": "true"})[2].register.call_count)


class DeadmanPipeTestCase(TestCase):
    """The write end must not escape into any process that outlives us.

    This is the one detail that disables the whole deadman *silently*: the supervisor waits for
    EOF on a pipe whose write end only this process should hold, and
    `autoreload.restart_with_reloader` spawns the runserver child with **`close_fds=False`** --
    so anything inheritable is handed to a process that lives as long as the server does, and
    the read would then never return.

    What saves it is PEP 446: `os.pipe()` fds are non-inheritable by default, and only the
    supervisor's read end is made inheritable, by `pass_fds`. That is a property of the
    standard library rather than of this code, which is exactly why it is worth pinning.
    """

    @override_settings(TASKS={"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}})
    def test_the_write_end_is_not_inheritable(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(start.subprocess, "Popen"):
            _process, write_fd = start._spawn_worker()
        self.addCleanup(os.close, write_fd)
        self.assertFalse(os.get_inheritable(write_fd))


class StopWorkerTestCase(unittest.TestCase):
    """SIGTERM to the group, a grace, then SIGKILL.

    `killpg`, never `process.kill()`: the supervisor holds the worker as a child in its group,
    and signalling only the leader leaves a worker running with no parent -- the orphan the
    original refusal to spawn one was worried about.
    """

    def _process(self, poll=None, times_out=True):
        process = mock.Mock()
        process.pid = 4321
        process.poll.return_value = poll
        if times_out:
            process.wait.side_effect = start.subprocess.TimeoutExpired("db_worker", 5)
        return process

    def test_a_worker_that_stops_politely_is_not_killed(self):
        process = self._process(times_out=False)
        with mock.patch.object(start.os, "killpg") as killpg, \
                mock.patch.object(start.os, "getpgid", return_value=4321):
            start._stop_worker(process, threading.Event())
        self.assertEqual([signal.SIGTERM], [c.args[1] for c in killpg.call_args_list])

    def test_one_that_does_not_is(self):
        process = self._process()
        with mock.patch.object(start.os, "killpg") as killpg, \
                mock.patch.object(start.os, "getpgid", return_value=4321):
            start._stop_worker(process, threading.Event())
        self.assertEqual([signal.SIGTERM, signal.SIGKILL],
                         [c.args[1] for c in killpg.call_args_list])

    def test_an_already_dead_worker_is_left_alone(self):
        process = self._process(poll=0)
        with mock.patch.object(start.os, "killpg") as killpg:
            start._stop_worker(process, threading.Event())
        self.assertEqual(0, killpg.call_count)

    def test_it_never_raises(self):
        """`stop_if_owned`'s contract, for the same reason: it runs at exit, where an exception
        is noise on top of whatever the command was actually doing."""
        process = self._process()
        with mock.patch.object(start.os, "getpgid", side_effect=ProcessLookupError):
            start._stop_worker(process, threading.Event())


class WatchWorkerTestCase(unittest.TestCase):
    """A worker that dies three seconds in must not leave a normal-looking terminal."""

    def test_an_unexpected_exit_is_announced(self):
        said = []
        process = mock.Mock()
        process.wait.return_value = 1
        start._watch_worker(process, threading.Event(), said.append)
        self.assertIn("background worker exited", "".join(said).lower())

    def test_our_own_shutdown_is_silent(self):
        """Or every Ctrl-C would print it."""
        said = []
        process = mock.Mock()
        process.wait.return_value = -15
        stopping = threading.Event()
        stopping.set()
        start._watch_worker(process, stopping, said.append)
        self.assertEqual([], said)


@override_settings(TASKS={"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}})
class WorkerFlagsTestCase(TestCase):

    def test_db_worker_still_understands_no_reload(self):
        """Passed explicitly because its default is `settings.DEBUG`, and its own help says
        why not: "tasks may not be stopped cleanly". A dependency bump that renamed or dropped
        the flag would otherwise fail at somebody's launch rather than here."""
        from django_tasks_db.management.commands.db_worker import Command as Worker

        options = Worker().create_parser("", "db_worker").parse_args(["--no-reload"])
        self.assertFalse(options.reload)

    def test_it_cannot_even_be_parsed_under_another_backend(self):
        """Which is why `start` gates on the backend rather than spawning and hoping.

        `--backend` defaults to the alias `default` and validates it eagerly, so under
        `ImmediateBackend` the command dies in argparse before its first poll -- on every
        launch, with a message about backends that says nothing about why a dev server printed
        it. Found by writing the test above without the override.
        """
        from django.core.management.base import CommandError

        from django_tasks_db.management.commands.db_worker import Command as Worker

        with override_settings(TASKS={"default": {
                "BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}):
            with self.assertRaises(CommandError) as caught:
                Worker().create_parser("", "db_worker").parse_args(["--no-reload"])
        self.assertIn("not a database backend", str(caught.exception))

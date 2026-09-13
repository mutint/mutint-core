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

    def test_a_restart_from_the_page_opens_no_browser_and_still_migrates(self):
        """Pressing Restart on /update/ is how a staged update gets applied, so the migrate
        has to survive the gate -- that launch is the whole point. What must not happen is a
        second browser window, navigating the page that is polling for the server away to the
        site root. See `update.note_restart`."""
        command = start.Command()
        command.stdout = StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(start, "call_command") as call, \
                mock.patch.object(start, "_asked_for_this_restart", return_value=True), \
                mock.patch.object(start.threading, "Timer") as timer:
            command.handle()

        self.assertIn("migrate", [c.args[0] for c in call.call_args_list])
        self.assertEqual(0, timer.call_count)

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

    @override_settings(MUTINT_WORKERS=1)
    def test_a_launch_starts_exactly_one_supervisor(self):
        popen, _call, _at_exit, out = self._run({})
        self.assertEqual(1, popen.call_count)
        self.assertIn("Background worker running", out)

    @override_settings(MUTINT_WORKERS=4)
    def test_one_supervisor_whatever_the_count(self):
        """The architectural decision, made checkable. The pool is N children of one
        supervisor because the shutdown order -- workers, then group, then cluster -- is a
        global invariant that N supervisors would turn into a per-process one."""
        popen, _call, _at_exit, _out = self._run({})
        self.assertEqual(1, popen.call_count)

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

    @override_settings(MUTINT_WORKERS=3)
    def test_the_setting_decides_how_many(self):
        argv = self._run({})[0].call_args.args[0]
        self.assertEqual("3", argv[argv.index("--workers") + 1])

    @override_settings(MUTINT_WORKERS=4)
    def test_the_flag_beats_the_setting(self):
        argv = self._run({}, workers=2)[0].call_args.args[0]
        self.assertEqual("2", argv[argv.index("--workers") + 1])

    @override_settings(MUTINT_WORKERS=4)
    def test_no_worker_beats_both(self):
        """Not an argparse conflict: the realistic way to hit it is an alias carrying one flag
        and an environment carrying the other, and a dev server that refuses to start over a
        redundant flag is the wrong trade."""
        popen, _call, _at_exit, out = self._run({}, no_worker=True, workers=4)
        self.assertEqual(0, popen.call_count)
        self.assertIn("db_worker", out)

    @override_settings(MUTINT_WORKERS=0)
    def test_zero_is_the_same_as_no_worker(self):
        """An operator must be able to turn the pool off from the environment."""
        popen, _call, _at_exit, out = self._run({})
        self.assertEqual(0, popen.call_count)
        self.assertIn("No background worker", out)

    @override_settings(MUTINT_WORKERS="four")
    def test_a_nonsense_setting_does_not_stop_the_server(self):
        popen, call, _at_exit, out = self._run({})
        argv = popen.call_args.args[0]
        self.assertEqual("1", argv[argv.index("--workers") + 1])
        self.assertIn("not a number", out)
        self.assertIn("runserver", [c.args[0] for c in call.call_args_list])

    @override_settings(MUTINT_WORKERS=500)
    def test_a_huge_count_is_clamped_and_said_so(self):
        """A typo must not be able to exhaust the cluster's connection limit."""
        popen, _call, _at_exit, out = self._run({})
        argv = popen.call_args.args[0]
        self.assertEqual(str(start.MAX_WORKERS), argv[argv.index("--workers") + 1])
        self.assertIn("rather than 500", out)

    @override_settings(MUTINT_WORKERS=3)
    def test_the_banner_says_how_many(self):
        out = self._run({})[3]
        self.assertIn("3 background workers running", out)
        self.assertIn("supervisor pid", out)

    @override_settings(MUTINT_WORKERS=1)
    def test_the_banner_stays_singular_for_one(self):
        """Never "1 background workers". Pinned rather than left to the default, which is a
        function of this machine's core count: unpinned it would pass on a laptop and fail on a
        two-core CI box, or the other way about."""
        out = self._run({})[3]
        self.assertIn("Background worker running", out)
        self.assertNotIn("1 background workers", out)

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
            _process, write_fd = start._spawn_worker(1)
        self.addCleanup(os.close, write_fd)
        self.assertFalse(os.get_inheritable(write_fd))

    @override_settings(TASKS={"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}})
    def test_there_is_still_exactly_one_write_end(self):
        """A pool is N children of one supervisor. A later refactor giving each worker its own
        pipe would multiply the one detail that disables this silently."""
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(start.subprocess, "Popen") as popen:
            _process, write_fd = start._spawn_worker(4)
        self.addCleanup(os.close, write_fd)
        self.assertEqual(1, popen.call_count)
        self.assertEqual(1, len(popen.call_args.kwargs["pass_fds"]))


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
        self.assertIn("background workers have stopped", "".join(said).lower())

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


class DefaultWorkersTestCase(unittest.TestCase):
    """How many workers an installation that has said nothing gets.

    Scaled to the machine because the failure it answers is a queue that does not drain: with
    one worker a breseq run blocks every coverage build behind it for hours, on a laptop with
    cores to spare. Half of them, so PostgreSQL, runserver and whatever a task shells out to
    still have somewhere to run -- and capped, because what runs out first is memory rather
    than cores.
    """

    def _default(self, cpus, env=None):
        from mutint_common import base_settings
        with mock.patch.dict(os.environ, env or {}, clear=True), \
                mock.patch.object(base_settings.os, "cpu_count", return_value=cpus):
            return base_settings.default_workers()

    def test_it_is_half_the_cores_capped(self):
        self.assertEqual([1, 1, 1, 2, 4, 4],
                         [self._default(cpus) for cpus in (None, 1, 2, 4, 16, 64)])

    def test_never_none_and_never_zero(self):
        """`os.cpu_count()` can answer None, and a machine that cannot say how many cores it
        has must still run a worker."""
        self.assertEqual(1, self._default(None))

    def test_an_explicit_value_is_obeyed_above_the_cap(self):
        """The cap is on the guess, not on an answer somebody gave. `start` is what stops a
        typo, with a number and a sentence."""
        self.assertEqual(32, self._default(8, {"MUTINT_WORKERS": "32"}))

    def test_zero_is_kept(self):
        """Turning the pool off from the environment has to reach `start`, which treats it the
        same as --no-worker."""
        self.assertEqual(0, self._default(8, {"MUTINT_WORKERS": "0"}))

    def test_a_negative_clamps(self):
        self.assertEqual(0, self._default(8, {"MUTINT_WORKERS": "-4"}))

    def test_nonsense_falls_back_rather_than_raising(self):
        """Every other integer setting here would raise, which is right for them. This one is
        plausibly typed at a shell, and settings that refuse to load take the server down
        before anything can say why."""
        self.assertEqual(4, self._default(8, {"MUTINT_WORKERS": "four"}))

    def test_an_empty_value_falls_back_too(self):
        self.assertEqual(4, self._default(8, {"MUTINT_WORKERS": ""}))

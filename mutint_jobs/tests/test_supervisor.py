"""The deadman that stops the worker, and the cluster, when `./mutint start` dies.

`start` registers an `atexit` hook, and that covers more than it looks like: Django's
`run_with_reloader` installs `SIGTERM -> sys.exit(0)` and ends `except KeyboardInterrupt:
pass`, so Ctrl-C, SIGTERM and a closed terminal all exit normally and run it. What no hook can
cover is `SIGKILL` -- nothing inside a process acts after it is killed outright -- so the
watchdog has to be a separate process holding a pipe.

Nothing here starts a real worker or a real cluster, which is `test_pg.py`'s discipline for the
comparable module: test the decisions, not the spawning. The one claim with no test behind it
is the end-to-end `kill -9`, and it is called out in the plan as a by-hand check.
"""

import ast
import os
import signal
import subprocess
import unittest
from unittest import mock

from django.test import TestCase

from mutint_jobs import supervisor

SOURCE_PATH = supervisor.__file__


class ConstraintsTestCase(TestCase):
    """The two properties that let this run at all, and that nothing else would catch."""

    def test_it_imports_nothing_from_django(self):
        """It is launched by path, with no settings module and no `DJANGO_SETTINGS_MODULE`.

        Under an assembled project `mutint_jobs` is importable only because `config/settings.py`
        puts the submodule directories on `sys.path` -- which a process that never loads
        settings has not done. So a Django import here would not fail at some later, clearer
        moment; it would fail every time a worker was started, under MutInt and aledb
        and not under standalone mutint-core, which is the worst way to find out.
        """
        with open(SOURCE_PATH) as handle:
            tree = ast.parse(handle.read())

        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        self.assertEqual([], [n for n in imported if n.split(".")[0] == "django"])

    def test_it_runs_standalone(self):
        """The whole claim, checked the only way that cannot be fooled: run it."""
        result = subprocess.run(
            [os.sys.executable, SOURCE_PATH], capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "")})

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("usage:", result.stderr)

    def test_it_finds_pg_by_path(self):
        """`_stop_cluster` walks up from this file to `mutint_common/pg.py`. A move of either
        breaks it silently, because the function swallows everything -- it runs where there is
        nobody left to tell."""
        expected = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(SOURCE_PATH))),
            "mutint_common", "pg.py")
        self.assertTrue(os.path.exists(expected), expected)


class StopTestCase(unittest.TestCase):
    """SIGTERM, a grace, then SIGKILL -- and never wait out the task.

    A breseq run is hours. A Ctrl-C that hangs the terminal for hours is worse than an
    abandoned task, and the cost of that choice is a row left saying RUNNING for ever, which is
    what `./mutint reap_jobs` clears up after.
    """

    def _process(self, poll=None, times_out=True):
        process = mock.Mock()
        process.poll.return_value = poll
        if times_out:
            process.wait.side_effect = subprocess.TimeoutExpired("db_worker", 5)
        return process

    def test_a_polite_worker_gets_only_a_term(self):
        process = self._process(times_out=False)
        supervisor._stop(process)
        self.assertEqual([signal.SIGTERM], [c.args[0] for c in
                                            process.send_signal.call_args_list])

    def test_an_impolite_one_gets_a_kill(self):
        process = self._process()
        supervisor._stop(process)
        self.assertEqual([signal.SIGTERM, signal.SIGKILL],
                         [c.args[0] for c in process.send_signal.call_args_list])

    def test_one_that_has_already_gone_is_left_alone(self):
        process = self._process(poll=0)
        supervisor._stop(process)
        self.assertEqual(0, process.send_signal.call_count)


class WaitTestCase(unittest.TestCase):
    """What ends the wait, and which of the two endings it was."""

    def test_the_parent_dying_closes_the_pipe(self):
        """The whole mechanism: the kernel closes the write end however the parent died, and
        the read becomes ready at EOF. This simulates it by closing the end ourselves."""
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        os.close(write_fd)

        process = mock.Mock()
        process.poll.return_value = None
        self.assertTrue(supervisor._wait_for_parent(read_fd, process))

    def test_a_worker_that_exits_on_its_own_ends_the_wait_too(self):
        """Or `start` would watch a supervisor blocked for ever on a pipe and never learn that
        the worker behind it had died."""
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)

        process = mock.Mock()
        process.poll.return_value = 1
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            self.assertFalse(supervisor._wait_for_parent(read_fd, process))

    def test_a_live_parent_and_a_live_worker_keep_it_waiting(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)

        process = mock.Mock()
        process.poll.side_effect = [None, None, 0]
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            self.assertFalse(supervisor._wait_for_parent(read_fd, process))
        self.assertEqual(3, process.poll.call_count)


class ClusterHalfTestCase(unittest.TestCase):
    """The cluster is stopped only when it is ours to stop, and only through pg.py."""

    def test_it_does_nothing_when_it_was_told_nothing(self):
        """`start` passes `--pg-dir` only when it owns the cluster. An unowned one --
        `./mutint db start`, which exists so a server outlives one command -- must be left
        running."""
        with mock.patch("importlib.util.spec_from_file_location") as spec:
            supervisor._stop_cluster(None, None)
            supervisor._stop_cluster("/tmp/nowhere", None)
            supervisor._stop_cluster(None, 1234)
        self.assertEqual(0, spec.call_count)

    def test_it_never_raises(self):
        """It runs after the parent is already gone, so there is nobody to tell."""
        supervisor._stop_cluster("/definitely/not/a/checkout", 999999)


class UsageTestCase(unittest.TestCase):

    def test_it_refuses_without_a_pipe_and_an_entry_script(self):
        self.assertEqual(2, supervisor.main([]))
        self.assertEqual(2, supervisor.main(["7"]))


class StopGroupTestCase(unittest.TestCase):
    """Clearing the orphaned runserver child, and refusing to when it might be a shell.

    `restart_with_reloader` spawns runserver as an ordinary child of `./mutint start`, so a
    `kill -9` there orphans it still holding port 8000 -- which is what makes the next
    `./mutint start` fail with "That port is already in use". Pre-existing, and not something a
    deadman should leave behind.
    """

    def test_it_signals_the_group_it_was_given(self):
        with mock.patch.object(supervisor.os, "killpg") as killpg, \
                mock.patch.object(supervisor.time, "sleep"):
            supervisor._stop_group(4242)
        self.assertEqual([signal.SIGTERM, signal.SIGKILL],
                         [c.args[1] for c in killpg.call_args_list])

    def test_it_refuses_without_one(self):
        """`start` passes a pgid only when it was the group's *leader*, which distinguishes a
        group of us-and-our-children from the shell's group inherited under `sh -c`. Signalling
        the second would kill the user's shell, so the absent case must do nothing at all."""
        with mock.patch.object(supervisor.os, "killpg") as killpg:
            supervisor._stop_group(0)
        self.assertEqual(0, killpg.call_count)

    def test_it_never_raises(self):
        with mock.patch.object(supervisor.os, "killpg", side_effect=ProcessLookupError):
            supervisor._stop_group(4242)


class ParentStillAliveTestCase(unittest.TestCase):
    """Everything after the wait destroys something, so an unclear answer must stop it."""

    def test_a_live_parent_stops_the_cleanup(self):
        self.assertTrue(supervisor._alive_or_unknown(os.getpid()))

    def test_so_does_not_being_told_which_pid_to_watch(self):
        """Refusing then leaves the orphans this exists to clear up, which is what happens
        today anyway -- the safe direction."""
        self.assertTrue(supervisor._alive_or_unknown(None))

    def test_a_dead_parent_lets_it_run(self):
        done = subprocess.Popen([os.sys.executable, "-c", ""])
        done.wait()
        self.assertFalse(supervisor._alive_or_unknown(done.pid))


class OptionsTestCase(unittest.TestCase):
    """Each flag is independent: the group, the parent and the cluster are three separate
    things `start` may or may not have the right to clear up."""

    def test_they_are_pulled_out_and_the_positionals_survive(self):
        argv = ["--parent", "12", "7", "--group", "34", "./mutint", "--pg-dir", "/x"]

        self.assertEqual("12", supervisor._option(argv, "--parent"))
        self.assertEqual("34", supervisor._option(argv, "--group"))
        self.assertEqual("/x", supervisor._option(argv, "--pg-dir"))
        self.assertEqual(["7", "./mutint"], argv)

    def test_an_absent_one_takes_the_default(self):
        self.assertEqual(0, supervisor._option(["7", "./mutint"], "--group", 0))

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


class StopAllTestCase(unittest.TestCase):
    """A pool is stopped in one grace period, not in N of them.

    Serially, four workers that ignore SIGTERM would be forty seconds before the cluster is
    stopped -- forty seconds of the orphaned runserver child holding port 8000, which is the
    symptom `_stop_group` exists to prevent.
    """

    def _process(self, poll=None, times_out=True):
        process = mock.Mock()
        process.poll.return_value = poll
        if times_out:
            process.wait.side_effect = subprocess.TimeoutExpired("db_worker", 5)
        return process

    def test_every_worker_is_signalled(self):
        processes = [self._process(times_out=False) for _ in range(3)]
        supervisor._stop_all(processes)
        for process in processes:
            self.assertEqual([signal.SIGTERM],
                             [c.args[0] for c in process.send_signal.call_args_list])

    def test_they_are_all_signalled_before_any_is_waited_on(self):
        """The whole of what makes teardown cost one grace period rather than N."""
        order = []
        processes = []
        for index in range(3):
            process = mock.Mock()
            process.poll.return_value = None
            process.send_signal.side_effect = lambda sig, n=index: order.append(("signal", n))
            process.wait.side_effect = lambda timeout=None, n=index: order.append(("wait", n))
            processes.append(process)
        supervisor._stop_all(processes)
        self.assertEqual(["signal", "signal", "signal", "wait", "wait", "wait"],
                         [what for what, _n in order])

    def test_the_grace_is_one_shared_deadline(self):
        """Each worker's wait is bounded by what is left of the period, not given a fresh one --
        or the last of four would still be waiting twenty seconds in."""
        processes = [self._process() for _ in range(3)]
        supervisor._stop_all(processes, grace=0.05)
        for process in processes:
            timeouts = [c.kwargs["timeout"] for c in process.wait.call_args_list]
            self.assertTrue(all(timeout <= 0.05 for timeout in timeouts), timeouts)

    def test_the_stragglers_are_killed(self):
        processes = [self._process() for _ in range(2)]
        supervisor._stop_all(processes, grace=0.01)
        for process in processes:
            self.assertEqual([signal.SIGTERM, signal.SIGKILL],
                             [c.args[0] for c in process.send_signal.call_args_list])

    def test_the_ones_already_gone_are_left_alone(self):
        gone, live = self._process(poll=0), self._process(times_out=False)
        supervisor._stop_all([gone, live])
        self.assertEqual(0, gone.send_signal.call_count)
        self.assertEqual(1, live.send_signal.call_count)


class SpawnTestCase(unittest.TestCase):
    """What `main` puts on the machine, and what it must not."""

    def _main(self, argv_extra, poll=0):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        spawned = []

        def popen(argv, **kwargs):
            process = mock.Mock()
            process.poll.return_value = poll
            spawned.append((argv, kwargs, process))
            return process

        with mock.patch.object(supervisor.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(supervisor.signal, "signal"), \
                mock.patch.object(supervisor, "POLL_SECONDS", 0.01), \
                mock.patch.object(supervisor, "_announce"):
            supervisor.main([str(read_fd), "./mutint"] + argv_extra)
        return spawned

    def test_it_spawns_one_worker_per_requested_count(self):
        spawned = self._main(["--workers", "3"])
        self.assertEqual(3, len(spawned))
        for argv, _kwargs, _process in spawned:
            self.assertEqual(["db_worker", "--no-reload"], argv[2:4])

    def test_one_worker_when_nothing_says_otherwise(self):
        self.assertEqual(1, len(self._main([])))

    def test_a_count_below_one_still_spawns_one(self):
        """The supervisor clamps independently of `start`: it runs where nobody is left to be
        told that a nonsense value was ignored."""
        self.assertEqual(1, len(self._main(["--workers", "0"])))

    def test_each_worker_is_given_a_distinct_readable_id(self):
        """N workers write unprefixed INFO lines into one terminal, each carrying `worker_id=`.
        Without this that is 32 random characters per line."""
        spawned = self._main(["--workers", "3"])
        ids = [argv[argv.index("--worker-id") + 1] for argv, _k, _p in spawned]
        self.assertEqual(3, len(set(ids)))
        for name in ids:
            self.assertIn(str(os.getpid()), name)

    def test_no_workers_output_is_captured(self):
        """The N-fold form of "a worker that dies silently is worse than one you can see"."""
        for _argv, kwargs, _process in self._main(["--workers", "2"]):
            self.assertNotIn("stdout", kwargs)
            self.assertNotIn("stderr", kwargs)

    def test_a_worker_that_exits_is_never_respawned(self):
        """The promise this feature had to make: a pool is not supervision."""
        self.assertEqual(2, len(self._main(["--workers", "2"], poll=1)))


class WaitTestCase(unittest.TestCase):
    """What ends the wait, and which of the two endings it was."""

    def _pipe(self, close_write=False):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        if close_write:
            os.close(write_fd)
        else:
            self.addCleanup(os.close, write_fd)
        return read_fd

    def _worker(self, poll=None, side_effect=None):
        process = mock.Mock()
        if side_effect is not None:
            process.poll.side_effect = side_effect
        else:
            process.poll.return_value = poll
        return process

    def test_the_parent_dying_closes_the_pipe(self):
        """The whole mechanism: the kernel closes the write end however the parent died, and
        the read becomes ready at EOF. This simulates it by closing the end ourselves."""
        read_fd = self._pipe(close_write=True)
        self.assertTrue(supervisor._wait_for_parent(
            read_fd, [("w1", self._worker())], announce=lambda *a: None))

    def test_the_parent_dying_ends_it_with_workers_still_alive(self):
        """The teardown is about the parent, not about the pool: a full pool must not stop it
        being run."""
        read_fd = self._pipe(close_write=True)
        workers = [("w%d" % n, self._worker()) for n in range(3)]
        self.assertTrue(supervisor._wait_for_parent(read_fd, workers,
                                                    announce=lambda *a: None))

    def test_the_last_worker_exiting_ends_the_wait(self):
        """Or `start` would watch a supervisor blocked for ever on a pipe and never learn that
        the workers behind it had died."""
        read_fd = self._pipe()
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            self.assertFalse(supervisor._wait_for_parent(
                read_fd, [("w1", self._worker(poll=1))], announce=lambda *a: None))

    def test_one_worker_exiting_does_not_end_the_wait(self):
        """A pool of four must not be torn down because one of them crashed -- which is what
        exiting on the first death would do."""
        read_fd = self._pipe()
        alive = self._worker(side_effect=[None, None, 0])
        dead = self._worker(poll=1)
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            self.assertFalse(supervisor._wait_for_parent(
                read_fd, [("dead", dead), ("alive", alive)], announce=lambda *a: None))
        self.assertEqual(3, alive.poll.call_count)

    def test_a_worker_that_dies_is_announced(self):
        """The whole of what replaces restarting it: the pool shrinks and says so."""
        read_fd = self._pipe()
        said = []
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            supervisor._wait_for_parent(
                read_fd,
                [("dead", self._worker(poll=1)), ("alive", self._worker(side_effect=[None, 0]))],
                announce=lambda *args: said.append(args))
        self.assertEqual(("dead", 1, 1), said[0])
        self.assertEqual(("alive", 0, 0), said[1])

    def test_an_exit_is_announced_once_and_not_every_poll(self):
        """The obvious bug in this shape: a worker that died at second three would otherwise be
        reported once a second for the rest of the session."""
        read_fd = self._pipe()
        said = []
        alive = self._worker(side_effect=[None, None, None, 0])
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            supervisor._wait_for_parent(
                read_fd, [("dead", self._worker(poll=1)), ("alive", alive)],
                announce=lambda *args: said.append(args))
        self.assertEqual(1, len([call for call in said if call[0] == "dead"]))

    def test_a_live_parent_and_a_live_worker_keep_it_waiting(self):
        read_fd = self._pipe()
        process = self._worker(side_effect=[None, None, 0])
        with mock.patch.object(supervisor, "POLL_SECONDS", 0.01):
            self.assertFalse(supervisor._wait_for_parent(
                read_fd, [("w1", process)], announce=lambda *a: None))
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
        argv = ["--parent", "12", "7", "--group", "34", "./mutint", "--pg-dir", "/x",
                "--workers", "3"]

        self.assertEqual("12", supervisor._option(argv, "--parent"))
        self.assertEqual("34", supervisor._option(argv, "--group"))
        self.assertEqual("/x", supervisor._option(argv, "--pg-dir"))
        self.assertEqual("3", supervisor._option(argv, "--workers"))
        self.assertEqual(["7", "./mutint"], argv)

    def test_an_absent_one_takes_the_default(self):
        self.assertEqual(0, supervisor._option(["7", "./mutint"], "--group", 0))

    def test_an_absent_worker_count_means_one(self):
        """`start` passes it always, so this is what a hand-run supervisor gets -- and the two
        defaults have to agree."""
        self.assertEqual(1, supervisor._option(["7", "./mutint"], "--workers", 1))

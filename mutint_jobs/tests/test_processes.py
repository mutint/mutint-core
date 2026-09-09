"""Running a tool from a job: its output, and stopping it.

These moved here with `run_breseq_process` itself, from `mutint_breseq.tests.test_cancel`.
They exercise the loop against a **real process**, because the failure worth catching --
signalling the parent and leaving its children alive -- is invisible to anything that mocks
the subprocess away.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

from django.test import SimpleTestCase

from mutint_jobs import processes
from mutint_jobs.tests import fake_tool


def alive(pid):
    """Whether `pid` is a live process. Signal 0 checks without delivering anything."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def wait_until_gone(pid, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


class RunToolTestCase(SimpleTestCase):
    """`run_tool` alone: no database, no job, no task."""

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.tool = fake_tool.install(self.work)
        self.log_path = os.path.join(self.work, "job.log")
        self.pids_file = os.path.join(self.work, "pids.json")
        self.env = dict(os.environ, FAKE_TOOL_PIDS=self.pids_file)

    def _run(self, *args, **kwargs):
        kwargs.setdefault("env", self.env)
        kwargs.setdefault("poll_seconds", 0.1)
        with open(self.log_path, "ab") as log:
            return processes.run_tool([self.tool] + list(args), log, **kwargs)

    def _log(self):
        with open(self.log_path) as handle:
            return handle.read()

    def _pids(self, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if os.path.isfile(self.pids_file) and os.path.getsize(self.pids_file):
                with open(self.pids_file) as handle:
                    try:
                        return json.load(handle)
                    except ValueError:
                        pass
            time.sleep(0.05)
        self.fail("the fake tool never reported its pids")

    def test_the_command_line_opens_the_log(self):
        """The first question about a failed run is what was actually run."""
        self._run("--flag", "value")

        self.assertTrue(self._log().startswith("$ %s --flag value\n" % self.tool))

    def test_both_streams_land_in_the_log(self):
        self._run()
        written = self._log()

        self.assertIn("out: ", written)
        self.assertIn("err: something on the other stream", written)

    def test_the_returncode_is_what_comes_back(self):
        """`run_tool` returns a code and nothing else -- the output is in the log."""
        self.assertEqual(0, self._run())
        self.assertEqual(3, self._run(env=dict(self.env, FAKE_TOOL_FAIL="3")))

    def test_a_log_with_no_descriptor_is_allowed(self):
        """A task with no `Job` gets `logs._NullLog`, and must still be able to run a tool."""
        from mutint_jobs.logs import _NullLog

        self.assertEqual(0, processes.run_tool([self.tool], _NullLog(), env=self.env,
                                               poll_seconds=0.1))

    def test_cancelling_kills_the_process_and_its_children(self):
        """The assertion the whole `killpg` design exists for.

        `process.kill()` would pass a test that checked only the parent, and would leave
        bowtie2 and samtools running on a real machine while the page said the job had
        stopped. So the child is asserted dead too.
        """
        pids = {}

        def is_cancelled():
            # Cancel as soon as the fake has spawned its child and told us both pids.
            if not pids:
                pids.update(self._pids())
            return True

        with self.assertRaises(processes.Cancelled):
            self._run(env=dict(self.env, FAKE_TOOL_SLEEP="1"), timeout=60,
                      is_cancelled=is_cancelled)

        self.assertTrue(wait_until_gone(pids["parent"]), "the tool itself survived")
        self.assertTrue(wait_until_gone(pids["child"]),
                        "a child of the tool survived -- the signal did not reach the group")

    def test_a_run_nobody_cancels_completes_normally(self):
        self.assertEqual(0, self._run(timeout=60, is_cancelled=lambda: False))
        self.assertIn("done", self._log())

    def test_the_timeout_also_stops_the_group(self):
        pids = {}

        def capture():
            if not pids:
                pids.update(self._pids())
            return False

        with self.assertRaises(subprocess.TimeoutExpired):
            self._run(env=dict(self.env, FAKE_TOOL_SLEEP="1"), timeout=1,
                      is_cancelled=capture)

        self.assertTrue(wait_until_gone(pids["parent"]))
        self.assertTrue(wait_until_gone(pids["child"]))

    def test_no_cancellation_callback_is_allowed(self):
        # A task that does not offer to be cancelled still has to be runnable.
        self.assertEqual(0, self._run(timeout=60))

    def test_no_timeout_is_allowed(self):
        """`timeout=None` means "however long it takes", which is the honest default for a
        caller that has no budget to enforce."""
        self.assertEqual(0, self._run(timeout=None))

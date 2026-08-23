"""`./aledb start` under the autoreloader.

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
import unittest
from io import StringIO
from unittest import mock

from django.test import TestCase

from aledb_common.management.commands import start


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

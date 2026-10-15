"""Which command opens the browser, decided per platform.

`./mutint start` ran macOS's `open` and nothing else, so under WSL and on Linux no browser
appeared and nothing said why. These pin the decision table in `mutint_common.browser`.
"""

import os
import subprocess
import unittest
from unittest import mock

from mutint_common import browser

URL = "http://127.0.0.1:8000"


class BrowserCommandTestCase(unittest.TestCase):

    def _command(self, platform, wsl=False, on_path=(), env=None):
        which = lambda name: f"/usr/bin/{name}" if name in on_path else None
        with mock.patch.object(browser.sys, "platform", platform), \
                mock.patch.object(browser, "is_wsl", return_value=wsl), \
                mock.patch.object(browser.shutil, "which", side_effect=which), \
                mock.patch.dict(os.environ, env or {}, clear=True):
            return browser.browser_command(URL)

    def test_a_mac_uses_open(self):
        self.assertEqual(["open", URL], self._command("darwin"))

    def test_wsl_uses_wslview(self):
        self.assertEqual(["wslview", URL],
                         self._command("linux", wsl=True, on_path=("wslview", "xdg-open")))

    def test_wsl_without_wslu_asks_powershell(self):
        argv = self._command("linux", wsl=True, on_path=("powershell.exe",))
        self.assertEqual("powershell.exe", argv[0])
        self.assertEqual(["Start-Process", URL], argv[-2:])

    def test_wsl_with_no_interop_has_no_way(self):
        self.assertIsNone(self._command("linux", wsl=True))

    def test_wsl_beats_a_display(self):
        """WSLg sets DISPLAY, and `xdg-open` there without wslu opens nothing: the browser to
        open is Windows', so WSL is decided before the generic Linux branch."""
        self.assertEqual(["wslview", URL],
                         self._command("linux", wsl=True, on_path=("wslview", "xdg-open"),
                                       env={"DISPLAY": ":0"}))

    def test_a_linux_desktop_uses_xdg_open(self):
        self.assertEqual(["xdg-open", URL],
                         self._command("linux", on_path=("xdg-open",), env={"DISPLAY": ":0"}))

    def test_wayland_counts_as_a_desktop(self):
        self.assertEqual(["xdg-open", URL],
                         self._command("linux", on_path=("xdg-open",),
                                       env={"WAYLAND_DISPLAY": "wayland-0"}))

    def test_a_headless_box_opens_nothing_even_with_a_console_browser(self):
        """The tripwire against swapping in the stdlib `webbrowser` module, which would start
        lynx in the terminal running the server."""
        self.assertIsNone(self._command("linux", on_path=("lynx", "www-browser", "xdg-open")))


class IsWslTestCase(unittest.TestCase):

    def _is_wsl(self, release):
        with mock.patch("builtins.open", mock.mock_open(read_data=release)):
            return browser.is_wsl()

    def test_wsl2(self):
        self.assertTrue(self._is_wsl("5.15.90.1-microsoft-standard-WSL2\n"))

    def test_wsl1(self):
        self.assertTrue(self._is_wsl("4.4.0-19041-Microsoft\n"))

    def test_a_plain_linux(self):
        self.assertFalse(self._is_wsl("6.8.0-45-generic\n"))

    def test_no_proc_is_not_wsl(self):
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            self.assertFalse(browser.is_wsl())


class OpenBrowserTestCase(unittest.TestCase):

    def test_runs_the_command_with_its_output_captured(self):
        with mock.patch.object(browser, "browser_command", return_value=["open", URL]), \
                mock.patch.object(browser.subprocess, "run") as run:
            self.assertTrue(browser.open_browser(URL))
        self.assertEqual((["open", URL],), run.call_args.args)
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_no_command_runs_nothing(self):
        with mock.patch.object(browser, "browser_command", return_value=None), \
                mock.patch.object(browser.subprocess, "run") as run:
            self.assertFalse(browser.open_browser(URL))
        self.assertEqual(0, run.call_count)

    def test_a_vanished_command_does_not_raise(self):
        """It runs in a Timer thread, where a traceback is noise over the server's banner."""
        with mock.patch.object(browser, "browser_command", return_value=["open", URL]), \
                mock.patch.object(browser.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(browser.open_browser(URL))

    def test_subprocess_is_the_real_one(self):
        self.assertIs(subprocess, browser.subprocess)

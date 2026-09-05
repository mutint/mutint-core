"""Finding external tools.

The suite had no external-tool dependency before this, so there was no precedent for where to
look or how to fail. These pin both: the managed environment wins over PATH, and a missing tool
says what to run rather than dying somewhere downstream.
"""

import os
import stat
import tempfile

from django.test import TestCase, override_settings

from mutint_common import tools


def _executable(directory, name):
    os.makedirs(os.path.join(directory, "bin"), exist_ok=True)
    path = os.path.join(directory, "bin", name)
    with open(path, "w") as handle:
        handle.write("#!/bin/sh\n")
    os.chmod(path, stat.S_IRWXU)
    return path


class ToolPathTestCase(TestCase):
    def setUp(self):
        self.tools_dir = tempfile.mkdtemp()

    def test_the_managed_environment_is_used_when_it_has_the_tool(self):
        expected = _executable(self.tools_dir, "bedtools")
        with override_settings(MUTINT_TOOLS_DIR=self.tools_dir):
            self.assertEqual(tools.tool_path("bedtools"), expected)

    def test_the_managed_environment_wins_over_path(self):
        """A project that pinned a version gets that one, not whatever the developer has."""
        expected = _executable(self.tools_dir, "sh")  # certain to also be on PATH
        with override_settings(MUTINT_TOOLS_DIR=self.tools_dir):
            self.assertEqual(tools.tool_path("sh"), expected)

    def test_path_is_the_fallback(self):
        """So a developer's own install still works without waiting for a solve."""
        with override_settings(MUTINT_TOOLS_DIR=self.tools_dir):
            found = tools.tool_path("sh")
        self.assertIsNotNone(found)
        self.assertNotIn(self.tools_dir, found)

    def test_an_unknown_tool_is_none(self):
        with override_settings(MUTINT_TOOLS_DIR=self.tools_dir):
            self.assertIsNone(tools.tool_path("no-such-tool-anywhere"))

    def test_no_tools_setting_falls_back_rather_than_raising(self):
        """A deployment that never ran the installer still finds tools on PATH."""
        with override_settings(MUTINT_TOOLS_DIR=None):
            self.assertIsNotNone(tools.tool_path("sh"))
            self.assertIsNone(tools.tool_path("no-such-tool-anywhere"))

    def test_require_names_the_command_that_installs_it(self):
        with override_settings(MUTINT_TOOLS_DIR=self.tools_dir):
            with self.assertRaises(tools.ToolMissing) as caught:
                tools.require("no-such-tool-anywhere")

        message = str(caught.exception)
        self.assertIn("no-such-tool-anywhere", message)
        self.assertIn("install", message)

    def test_have_is_all_or_nothing(self):
        _executable(self.tools_dir, "bedtools")
        with override_settings(MUTINT_TOOLS_DIR=self.tools_dir):
            self.assertTrue(tools.have("bedtools"))
            self.assertFalse(tools.have("bedtools", "no-such-tool-anywhere"))

"""`./aledb version`, and the bump that rewrites version.py.

The bump is exercised against a copy, never the real file -- a test that edits
aledb_common/version.py would leave the working tree dirty and, worse, would
change the version the rest of the suite asserts on.
"""

import os
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from aledb_common.management.commands.version import bump, components, rewrite
from aledb_common.version import __version__


class BumpTestCase(unittest.TestCase):

    def test_patch(self):
        self.assertEqual("1.1.1", bump("1.1.0", "patch"))

    def test_minor_zeroes_the_patch(self):
        self.assertEqual("1.2.0", bump("1.1.7", "minor"))

    def test_major_zeroes_everything_after_it(self):
        self.assertEqual("2.0.0", bump("1.7.7", "major"))

    def test_a_non_numeric_version_is_refused(self):
        with self.assertRaises(CommandError):
            bump("1.1.0-rc1", "patch")

    def test_a_two_part_version_is_refused(self):
        """Better to fail loudly than to guess which part 'minor' means."""
        with self.assertRaises(CommandError):
            bump("1.1", "minor")


class RewriteTestCase(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.path = os.path.join(self.directory, "version.py")

    def _write(self, text):
        with open(self.path, "w") as handle:
            handle.write(text)

    def _read(self):
        with open(self.path) as handle:
            return handle.read()

    def test_it_replaces_the_assignment(self):
        self._write('"""Docstring."""\n\n__version__ = "1.1.0"\n')
        rewrite(self.path, "1.2.0")
        self.assertIn('__version__ = "1.2.0"', self._read())

    def test_it_leaves_the_rest_of_the_file_alone(self):
        self._write('"""Docstring mentioning 1.1.0."""\n\n'
                    '__version__ = "1.1.0"\n\n\ndef get_version():\n    return __version__\n')
        rewrite(self.path, "1.2.0")
        content = self._read()
        self.assertIn("Docstring mentioning 1.1.0.", content)
        self.assertIn("def get_version():", content)
        self.assertEqual(1, content.count('"1.2.0"'))

    def test_a_file_with_no_assignment_is_an_error(self):
        self._write("# nothing to bump here\n")
        with self.assertRaises(CommandError):
            rewrite(self.path, "1.2.0")


class VersionCommandTestCase(unittest.TestCase):

    def test_it_prints_aledb_cores_version(self):
        """Among whatever else is installed.

        The command lists every component that exposes a version, which is the point of it
        -- an assembled project adds its own, so `mutint-app 0.0.1` is a second line there.
        This used to assertEqual on the whole output, which quietly meant "and nothing else
        is installed" and failed the moment a bare `./mutint test` began running it.
        """
        out = StringIO()
        call_command("version", stdout=out)

        lines = out.getvalue().strip().splitlines()
        self.assertIn("aledb-core %s" % __version__, lines)

    def test_the_cli_routes_around_djangos_reserved_name(self):
        """`version` is reserved: ManagementUtility answers it with Django's own
        version before app commands are consulted, so `./aledb version` would print
        e.g. 4.2.30. aledb_common/cli.py dispatches ours directly instead.

        Goes through manage() rather than call_command, because call_command skips
        ManagementUtility altogether and so could not catch this regressing.
        """
        import django
        from aledb_common.cli import manage

        out = StringIO()
        with mock.patch.object(sys, "argv", ["aledb", "version"]), \
                mock.patch.object(sys, "stdout", out):
            manage()

        printed = out.getvalue()
        self.assertIn("aledb-core %s" % __version__, printed)
        self.assertNotIn(django.get_version(), printed)


class ComponentsTestCase(unittest.TestCase):
    """An assembled project versions itself the way aledb-core does.

    Discovery is by convention -- an installed app exposing __version__ from a
    `version` submodule -- rather than a registry, because an app being in
    INSTALLED_APPS already says it is part of this project.
    """

    def test_aledb_core_is_always_a_component(self):
        self.assertIn("aledb-core", [name for name, _m, _v in components()])

    def test_it_reports_aledb_cores_real_version(self):
        found = {name: version for name, _m, version in components()}
        self.assertEqual(__version__, found["aledb-core"])

    def test_aledb_common_is_not_listed_twice(self):
        """aledb_common holds core's version.py; without the skip it would appear
        once as an app component and again as aledb-core itself."""
        names = [name for name, _m, _v in components()]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("aledb_common", names)


class BumpTargetTestCase(unittest.TestCase):
    """--component is required when the choice is ambiguous."""

    def _bump(self, **kwargs):
        out = StringIO()
        call_command("version", bump="patch", stdout=out, **kwargs)
        return out.getvalue()

    def test_an_unknown_component_is_refused(self):
        with self.assertRaises(CommandError) as caught:
            self._bump(component="NoSuchThing")
        self.assertIn("no such component", str(caught.exception))

    def test_standalone_core_needs_no_component(self):
        """Only aledb-core is installed here, so there is nothing to disambiguate.

        Bumps a copy: pointing the command at the real version.py would leave the
        working tree dirty and move the version the rest of the suite asserts on.
        """
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "version.py")
        with open(path, "w") as handle:
            handle.write('__version__ = "%s"\n' % __version__)

        with mock.patch("aledb_common.management.commands.version.components",
                        return_value=[("aledb-core", mock.Mock(__file__=path), "1.1.0")]):
            printed = self._bump()

        self.assertIn("aledb-core 1.1.0 -> 1.1.1", printed)
        with open(path) as handle:
            self.assertIn('__version__ = "1.1.1"', handle.read())

    def test_two_components_force_an_explicit_choice(self):
        """Bumping the wrong repo's version is silent, and only shows up at release."""
        two = [("MutInt", mock.Mock(__file__="/x/version.py"), "0.0.1"),
               ("aledb-core", mock.Mock(__file__="/y/version.py"), "1.1.0")]
        with mock.patch("aledb_common.management.commands.version.components",
                        return_value=two):
            with self.assertRaises(CommandError) as caught:
                self._bump()
        self.assertIn("--component is required", str(caught.exception))
        self.assertIn("MutInt", str(caught.exception))

"""`./mutint version`, and the bump that rewrites version.py.

The bump is exercised against a copy, never the real file -- a test that edits
mutint_common/version.py would leave the working tree dirty and, worse, would
change the version the rest of the suite asserts on.
"""

import os
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from mutint_common.management.commands.version import (
    aggregator, bump, components, rewrite,
)
from mutint_common.version import __version__


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

    def test_it_prints_mutint_cores_version(self):
        """Among whatever else is installed.

        The command lists every component that exposes a version, which is the point of it
        -- an assembled project adds its own, so `mutint-app 0.0.1` is a second line there.
        This used to assertEqual on the whole output, which quietly meant "and nothing else
        is installed" and failed the moment a bare `./mutint test` began running it.
        """
        out = StringIO()
        call_command("version", stdout=out)

        lines = out.getvalue().strip().splitlines()
        self.assertIn("mutint-core %s" % __version__, lines)

    def test_the_cli_routes_around_djangos_reserved_name(self):
        """`version` is reserved: ManagementUtility answers it with Django's own
        version before app commands are consulted, so `./mutint version` would print
        e.g. 4.2.30. mutint_common/cli.py dispatches ours directly instead.

        Goes through manage() rather than call_command, because call_command skips
        ManagementUtility altogether and so could not catch this regressing.
        """
        import django
        from mutint_common.cli import manage

        out = StringIO()
        with mock.patch.object(sys, "argv", ["mutint", "version"]), \
                mock.patch.object(sys, "stdout", out):
            manage()

        printed = out.getvalue()
        self.assertIn("mutint-core %s" % __version__, printed)
        self.assertNotIn(django.get_version(), printed)


class ComponentsTestCase(unittest.TestCase):
    """An assembled project versions itself the way mutint-core does.

    Discovery is by convention -- an installed app exposing __version__ from a
    `version` submodule -- rather than a registry, because an app being in
    INSTALLED_APPS already says it is part of this project.
    """

    def test_mutint_core_is_always_a_component(self):
        self.assertIn("mutint-core", [name for name, _m, _v in components()])

    def test_it_reports_mutint_cores_real_version(self):
        found = {name: version for name, _m, version in components()}
        self.assertEqual(__version__, found["mutint-core"])

    def test_mutint_common_is_not_listed_twice(self):
        """mutint_common holds core's version.py; without the skip it would appear
        once as an app component and again as mutint-core itself."""
        names = [name for name, _m, _v in components()]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("mutint_common", names)


class AggregatorTestCase(unittest.TestCase):
    """The assembled project's own version, which is not an app's.

    MutInt and ALEdb own only `config/`, so nothing in `apps.get_app_configs()` carries
    their version and `./mutint version` reported every component except the one whose
    name is on the sidebar. These pin the lookup that fixes it.
    """

    def test_standalone_mutint_core_has_no_aggregator(self):
        """core's own config package deliberately has no version.py, so there is no
        assembly to report -- and `--bump` stays unambiguous here as a result."""
        self.assertIsNone(aggregator())

    def test_it_reads_the_settings_package_not_a_hardcoded_name(self):
        module = mock.Mock(__version__="9.9.9", NAME="Assembled")
        with mock.patch.object(settings, "SETTINGS_MODULE", "someproject.settings"), \
                mock.patch("importlib.import_module", return_value=module) as imported:
            self.assertEqual(("Assembled", module, "9.9.9"), aggregator())
        imported.assert_called_once_with("someproject.version")

    def test_it_falls_back_to_the_package_name(self):
        module = mock.Mock(__version__="9.9.9", spec=["__version__"])
        with mock.patch.object(settings, "SETTINGS_MODULE", "config.settings"), \
                mock.patch("importlib.import_module", return_value=module):
            self.assertEqual("config", aggregator()[0])

    def test_a_version_module_without_a_version_is_not_a_component(self):
        module = mock.Mock(spec=[])
        with mock.patch.object(settings, "SETTINGS_MODULE", "config.settings"), \
                mock.patch("importlib.import_module", return_value=module):
            self.assertIsNone(aggregator())

    def test_the_aggregator_leads_the_component_list(self):
        """It is the thing whose name is on the sidebar, so it reads first."""
        top = ("Assembled", mock.Mock(__version__="9.9.9"), "9.9.9")
        with mock.patch("mutint_common.management.commands.version.aggregator",
                        return_value=top):
            names = [name for name, _m, _v in components()]
        self.assertEqual("Assembled", names[0])
        self.assertEqual("mutint-core", names[-1])


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
        """Only mutint-core is installed here, so there is nothing to disambiguate.

        Bumps a copy: pointing the command at the real version.py would leave the
        working tree dirty and move the version the rest of the suite asserts on.
        """
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "version.py")
        with open(path, "w") as handle:
            handle.write('__version__ = "%s"\n' % __version__)

        with mock.patch("mutint_common.management.commands.version.components",
                        return_value=[("mutint-core", mock.Mock(__file__=path), "1.1.0")]):
            printed = self._bump()

        self.assertIn("mutint-core 1.1.0 -> 1.1.1", printed)
        with open(path) as handle:
            self.assertIn('__version__ = "1.1.1"', handle.read())

    def test_two_components_force_an_explicit_choice(self):
        """Bumping the wrong repo's version is silent, and only shows up at release."""
        two = [("MutInt", mock.Mock(__file__="/x/version.py"), "0.0.1"),
               ("mutint-core", mock.Mock(__file__="/y/version.py"), "1.1.0")]
        with mock.patch("mutint_common.management.commands.version.components",
                        return_value=two):
            with self.assertRaises(CommandError) as caught:
                self._bump()
        self.assertIn("--component is required", str(caught.exception))
        self.assertIn("MutInt", str(caught.exception))

"""The database lifecycle, tested without starting a database.

`aledb_common/pg.py` is loaded by the entry script *before* the venv exists, so most of what
can go wrong with it is not the kind of thing a running server would reveal: a syntax error
under an older interpreter, an import of something that is not installed yet, a socket path
that silently exceeds what the operating system accepts. Those are what this covers.

The lifecycle lives in a module rather than in the entry script precisely so that it can be
covered at all -- the script is three byte-identical copies with no tests.
"""

import ast
import os

from django.test import TestCase

from aledb_common import pg

SOURCE_PATH = pg.__file__.replace(".pyc", ".py")


class ParseabilityTestCase(TestCase):
    """Constraints that hold before Python or Django are available."""

    def test_it_parses_on_python_39(self):
        """The entry script provisions the interpreter, so it runs under whatever python3 the
        host has -- 3.9 on a current macOS -- and loads this module by path to do it. Syntax
        newer than that fails on exactly the machines that have not upgraded yet, which is to
        say every new checkout, and never on the machine of whoever wrote it.
        """
        with open(SOURCE_PATH) as handle:
            source = handle.read()

        ast.parse(source, feature_version=(3, 9))

    def test_it_imports_nothing_from_django(self):
        """It is loaded before the venv exists. A Django import here would not fail at some
        later, clearer moment -- it would fail on the first command anybody ran."""
        with open(SOURCE_PATH) as handle:
            tree = ast.parse(handle.read())

        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        self.assertEqual([], [name for name in imported if name.split(".")[0] == "django"])


class DatabaseNameTestCase(TestCase):

    def test_it_comes_from_the_checkout(self):
        self.assertEqual("aledb_core", pg.database_name("/srv/aledb-core"))
        self.assertEqual("mutint", pg.database_name("/srv/mutint"))
        self.assertEqual("aledb_deploy", pg.database_name("/srv/aledb-deploy"))

    def test_the_three_checkouts_differ(self):
        """Which is the point. Locally each has its own cluster and the name barely matters;
        pointed at one external server, a shared default would have them share a database."""
        names = {pg.database_name(path)
                 for path in ("/srv/aledb-core", "/srv/mutint", "/srv/aledb-deploy")}

        self.assertEqual(3, len(names))

    def test_it_is_a_legal_identifier(self):
        self.assertEqual("weird_name_here", pg.database_name("/srv/Weird Name.here/"))


class SocketPathTestCase(TestCase):

    def test_an_ordinary_checkout_keeps_its_socket_inside_env(self):
        cluster = pg.Cluster("/Users/someone/src/aledb-refactor/aledb-core")

        self.assertTrue(cluster.socket_dir.endswith("/env/pg"))

    def test_a_long_path_falls_back_before_the_limit_is_reached(self):
        """macOS caps sun_path at 104 bytes including the socket filename, and the failure is
        a postmaster that will not start. It cannot reproduce on a normal checkout, which is
        why it is asserted rather than discovered."""
        deep = "/Users/someone/Documents/Research Projects/" + ("subdirectory/" * 6) + "aledb-core"
        cluster = pg.Cluster(deep)

        self.assertTrue(cluster.socket_dir.startswith("/tmp/aledb-"),
                        cluster.socket_dir)
        self.assertLessEqual(len(cluster.socket_dir), pg.MAX_SOCKET_DIR)

    def test_the_fallback_is_stable_and_per_checkout(self):
        one = pg.Cluster("/very/long/" + "x" * 120 + "/one")
        two = pg.Cluster("/very/long/" + "x" * 120 + "/two")

        self.assertEqual(one.socket_dir, pg.Cluster(one.base_dir).socket_dir)
        self.assertNotEqual(one.socket_dir, two.socket_dir)

    def test_every_socket_directory_fits_sun_path(self):
        for path in ("/a", "/Users/someone/src/aledb-refactor/mutint", "/x" * 200):
            cluster = pg.Cluster(path)
            self.assertLessEqual(len(cluster.socket_dir), pg.MAX_SOCKET_DIR, path)


class ExternalServerTestCase(TestCase):
    """One variable decides who owns the server, so its edges are worth pinning."""

    def setUp(self):
        self.original = {key: os.environ.get(key)
                         for key in ("ALEDB_DB_HOST", "ALEDB_DB_MANAGED")}

    def tearDown(self):
        for key, value in self.original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set(self, host, managed):
        for key, value in (("ALEDB_DB_HOST", host), ("ALEDB_DB_MANAGED", managed)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_unset_means_manage_one_here(self):
        self._set(None, None)

        self.assertFalse(pg.is_external())

    def test_a_host_somebody_else_set_means_hands_off(self):
        self._set("db.example.org", None)

        self.assertTrue(pg.is_external())

    def test_our_own_export_is_not_somebody_else_s_server(self):
        """The half that is easy to miss: `ensure` exports ALEDB_DB_HOST for the cluster it
        just started, so without the MANAGED marker every question asked afterwards -- `db
        status` included -- would answer "external" about our own server."""
        self._set("/somewhere/env/pg", "1")

        self.assertFalse(pg.is_external())


class DescribeTestCase(TestCase):

    def test_it_answers_on_a_tree_where_nothing_is_provisioned(self):
        """Which is what makes `db status` usable as the first command anybody runs."""
        lines = pg.Cluster("/nonexistent/checkout").describe()

        self.assertIn("server:    not installed", lines)
        self.assertTrue(any(line.startswith("database:") for line in lines))

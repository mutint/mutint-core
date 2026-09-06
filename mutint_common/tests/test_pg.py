"""The database lifecycle, tested without starting a database.

`mutint_common/pg.py` is loaded by the entry script *before* the venv exists, so most of what
can go wrong with it is not the kind of thing a running server would reveal: a syntax error
under an older interpreter, an import of something that is not installed yet, a socket path
that silently exceeds what the operating system accepts. Those are what this covers.

The lifecycle lives in a module rather than in the entry script precisely so that it can be
covered at all -- the script is three byte-identical copies with no tests.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile

from django.test import TestCase

from mutint_common import pg

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
        self.assertEqual("mutint_core", pg.database_name("/srv/mutint-core"))
        self.assertEqual("mutint", pg.database_name("/srv/mutint"))
        self.assertEqual("aledb", pg.database_name("/srv/aledb"))

    def test_the_three_checkouts_differ(self):
        """Which is the point. Locally each has its own cluster and the name barely matters;
        pointed at one external server, a shared default would have them share a database."""
        names = {pg.database_name(path)
                 for path in ("/srv/mutint-core", "/srv/mutint", "/srv/aledb")}

        self.assertEqual(3, len(names))

    def test_it_is_a_legal_identifier(self):
        self.assertEqual("weird_name_here", pg.database_name("/srv/Weird Name.here/"))


class SocketPathTestCase(TestCase):

    def test_an_ordinary_checkout_keeps_its_socket_inside_env(self):
        cluster = pg.Cluster("/Users/someone/src/mutint-code/mutint-core")

        self.assertTrue(cluster.socket_dir.endswith("/env/pg"))

    def test_a_long_path_falls_back_before_the_limit_is_reached(self):
        """macOS caps sun_path at 104 bytes including the socket filename, and the failure is
        a postmaster that will not start. It cannot reproduce on a normal checkout, which is
        why it is asserted rather than discovered."""
        deep = "/Users/someone/Documents/Research Projects/" + ("subdirectory/" * 6) + "mutint-core"
        cluster = pg.Cluster(deep)

        self.assertTrue(cluster.socket_dir.startswith("/tmp/mutint-"),
                        cluster.socket_dir)
        self.assertLessEqual(len(cluster.socket_dir), pg.MAX_SOCKET_DIR)

    def test_the_fallback_is_stable_and_per_checkout(self):
        one = pg.Cluster("/very/long/" + "x" * 120 + "/one")
        two = pg.Cluster("/very/long/" + "x" * 120 + "/two")

        self.assertEqual(one.socket_dir, pg.Cluster(one.base_dir).socket_dir)
        self.assertNotEqual(one.socket_dir, two.socket_dir)

    def test_every_socket_directory_fits_sun_path(self):
        for path in ("/a", "/Users/someone/src/mutint-code/mutint", "/x" * 200):
            cluster = pg.Cluster(path)
            self.assertLessEqual(len(cluster.socket_dir), pg.MAX_SOCKET_DIR, path)


class ExternalServerTestCase(TestCase):
    """One variable decides who owns the server, so its edges are worth pinning."""

    def setUp(self):
        self.original = {key: os.environ.get(key)
                         for key in ("MUTINT_DB_HOST", "MUTINT_DB_MANAGED")}

    def tearDown(self):
        for key, value in self.original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set(self, host, managed):
        for key, value in (("MUTINT_DB_HOST", host), ("MUTINT_DB_MANAGED", managed)):
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
        """The half that is easy to miss: `ensure` exports MUTINT_DB_HOST for the cluster it
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


class StopIfOwnerIsTestCase(TestCase):
    """`stop_if_owned` asked on somebody else's behalf, for the deadman supervisor.

    That supervisor learns its parent has died -- however it died, `kill -9` included -- and
    then has to stop the cluster that parent owned. It cannot use `owned_by_me`: its own pid is
    not the owner's.

    Nothing here starts a cluster. `stop()` on a tree with no running postmaster takes its
    `is_running()` branch and simply disowns, so the *decision* is observable as whether the
    owner file survives, which is the only thing these tests are about.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.cluster = pg.Cluster(self.base)
        os.makedirs(os.path.dirname(self.cluster.owner_file), exist_ok=True)

    def _own(self, pid):
        with open(self.cluster.owner_file, "w") as handle:
            handle.write(str(pid))

    def _dead_pid(self):
        """A pid that certainly is not running: one we waited on."""
        done = subprocess.Popen([sys.executable, "-c", ""])
        done.wait()
        return done.pid

    def test_it_stops_a_cluster_its_dead_parent_owned(self):
        pid = self._dead_pid()
        self._own(pid)

        self.cluster.stop_if_owner_is(pid)

        self.assertFalse(os.path.exists(self.cluster.owner_file))

    def test_it_leaves_one_somebody_else_has_adopted(self):
        """The race this guards is real: another invocation reads the owner file, finds the pid
        dead and claims it, while we -- having read the same file a moment earlier -- stop the
        cluster underneath it. `_FileLock` is what makes the read and the stop one step; this
        check is what makes the answer right once we hold it."""
        self._own(os.getpid())

        self.cluster.stop_if_owner_is(self._dead_pid())

        self.assertTrue(os.path.exists(self.cluster.owner_file))

    def test_it_leaves_one_whose_owner_is_still_alive(self):
        """Pid reuse. If the number has been handed to a live process, this is not our parent
        and the cluster is not ours to stop."""
        self._own(os.getpid())

        self.cluster.stop_if_owner_is(os.getpid())

        self.assertTrue(os.path.exists(self.cluster.owner_file))

    def test_it_leaves_an_unowned_one_entirely(self):
        """`./mutint db start` creates one deliberately, so a server outlives one command.
        There is no owner file, so `owner()` answers None and equals no pid."""
        self.cluster.stop_if_owner_is(self._dead_pid())

        self.assertFalse(os.path.exists(self.cluster.owner_file))

    def test_it_does_nothing_without_a_pid(self):
        self._own(os.getpid())

        self.cluster.stop_if_owner_is(None)

        self.assertTrue(os.path.exists(self.cluster.owner_file))

    def test_it_never_raises(self):
        """`stop_if_owned`'s contract: it runs where there is nobody left to tell."""
        pg.Cluster("/definitely/not/a/checkout").stop_if_owner_is(1)


class DataDirectoryTestCase(TestCase):
    """The cluster is data and lives under data/db; env/ holds only what can be rebuilt."""

    def setUp(self):
        import tempfile, shutil
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_a_fresh_checkout_puts_the_cluster_under_data(self):
        cluster = pg.Cluster(self.base)
        self.assertEqual(os.path.join(self.base, "data", "db"), cluster.data)
        self.assertTrue(cluster.prefix.startswith(os.path.join(self.base, "env")))

    def test_the_file_store_defaults_beside_it(self):
        from mutint_common.base_settings import get_base_settings
        settings = get_base_settings(self.base)
        self.assertEqual(os.path.join(self.base, "data", "store"), settings["MUTINT_STORE_DIR"])

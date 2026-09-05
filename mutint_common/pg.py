"""The PostgreSQL cluster this checkout owns, under ``env/``.

MutInt is PostgreSQL-only. That would ordinarily cost the property mutint-core is built
around -- *clone it and run it, with no external services* -- so the database arrives the
way micromamba, Python and the external tools already do: a pinned prefix under ``env/``,
a cluster beside it, and a socket nobody else can reach. Nothing is installed on the host
and nothing listens on the network.

**Not a ``tools.txt`` entry.** That file means "binaries application code resolves through
``mutint_common.tools.tool_path()``", and no application code has any business calling
``pg_ctl``. Keeping the two apart also means a bioinformatics re-solve can never move the
database, and a deployment pointing at an external server downloads no Postgres at all.

**Not in the entry script**, though the entry script is what calls it. That file is three
byte-identical copies with no tests, and this is the subtlest code in the suite; here it can
be tested (``mutint_common/tests/test_pg.py``) without starting anything.

**Parseable on Python 3.9, and that is a real constraint rather than a nicety.** The entry
script loads this module *before* the venv exists, under whatever ``python3`` the host has
-- 3.9 on a current macOS. So: standard library only, no Django import, and no syntax newer
than 3.9. ``test_pg`` asserts the parse, because nothing else would catch it on a machine
that has already upgraded.

### Managed, or somebody else's

``MUTINT_DB_HOST`` decides, and one variable rather than two near-synonyms: **unset means
manage a local cluster here; set means hands off entirely.** A deployment exports it and
this module provisions nothing, starts nothing and stops nothing.

### Whose server is it

A cluster started by an invocation is **stopped when that invocation ends**, and a cluster
somebody else started is left alone. The ownership record is a pid file written by the
process that started it; ``os.execv`` preserves the pid, so it survives the entry script's
re-exec into the venv, and the reloader's child -- a different pid -- can never mistake
itself for the owner. ``mutint db start`` deliberately starts an *unowned* cluster, which is
how you keep one up across several commands.

A cluster whose owner is gone (``kill -9``, a crash) is **adopted rather than refused**: the
next invocation finds it running, sees a pid that no longer exists, and takes it over.
"""

import errno
import hashlib
import os
import re
import subprocess

#: The pinned spec. The major version is pinned deliberately: an unpinned solve would one day
#: install a newer major against an existing data directory, and Postgres refuses that outright
#: with "database files are incompatible with server" -- from a postmaster that has already
#: daemonised, so the message lands in the log where nobody looks. `check_version` turns that
#: into a sentence before anything starts.
SPEC = 'postgresql=18.*'

#: The role the cluster is created with and everything connects as. Not the OS user: a cluster
#: whose superuser is named after whoever ran initdb is one more thing that differs between
#: machines for no reason.
USER = 'mutint'

PORT = '5432'

#: macOS caps a unix socket path at 104 bytes including the NUL, and the socket file adds
#: `/.s.PGSQL.5432`. Directories longer than this get the /tmp fallback below.
_SOCKET_NAME_BYTES = len('/.s.PGSQL.5432') + 1
MAX_SOCKET_DIR = 104 - _SOCKET_NAME_BYTES


class PostgresError(RuntimeError):
    """Something the operator has to act on, phrased for them rather than for a log."""


def is_external():
    """Whether a server is being provided for us, in which case we manage nothing.

    `MUTINT_DB_MANAGED` is what distinguishes somebody else's host from the one we just
    exported for our own cluster -- `ensure` sets both, so without that second half a managed
    server reports itself as external the moment anything asks after it has started.
    """
    return (bool(os.environ.get('MUTINT_DB_HOST'))
            and os.environ.get('MUTINT_DB_MANAGED') != '1')


def database_name(base_dir):
    """The database name, derived from the checkout's directory name.

    Derived rather than configured, so `mutint-core`, `mutint` and `aledb` differ by
    default. Each already has its own cluster, so this barely matters locally -- it matters
    when three checkouts are pointed at *one* external server, where a shared default would
    have them silently share a database. It also removes a line from each assembled project's
    settings_local.py, which is one of the places the old aledb-deploy merge used to conflict every
    time.
    """
    return re.sub(r'\W', '_', os.path.basename(os.path.abspath(base_dir))).strip('_').lower()


class Cluster(object):
    """One checkout's cluster: where it lives, whether it is up, and how to say so."""

    def __init__(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)
        env = os.path.join(self.base_dir, 'env')
        self.prefix = os.path.join(env, 'postgres')
        self.data = os.path.join(env, 'pgdata')
        self.log = os.path.join(env, 'pg.log')
        self.owner_file = os.path.join(env, 'pg-owner')
        self.lock_file = os.path.join(env, 'pg.lock')
        self.name = database_name(self.base_dir)

    # -- where the socket goes ---------------------------------------------------------

    @property
    def socket_dir(self):
        """`env/pg`, or a hashed /tmp path when that would overflow `sun_path`.

        The three checkouts here land at 73-79 characters, under the cap but not by much: a
        checkout under something like "Documents/Research Projects" overflows it, and the
        failure is a postmaster that will not start.

        `/tmp` literally, **not `tempfile.gettempdir()`** -- on macOS that is a
        `/var/folders/…/T/` path of about 49 characters, which spends most of the headroom the
        fallback exists to create.
        """
        preferred = os.path.join(self.base_dir, 'env', 'pg')
        if len(preferred) <= MAX_SOCKET_DIR:
            return preferred
        digest = hashlib.sha256(self.base_dir.encode('utf-8')).hexdigest()[:12]
        return '/tmp/mutint-' + digest

    def _make_socket_dir(self):
        path = self.socket_dir
        if not os.path.isdir(path):
            os.makedirs(path, 0o700)
            return path
        # /tmp is world-writable, so a directory there that we did not create is not ours to
        # trust: with `trust` authentication, anyone who can write to it can plant a socket we
        # would then connect to. Cheap to check, and never added later.
        if os.stat(path).st_uid != os.getuid():
            raise PostgresError(
                "%s is not owned by you. Remove it, or set MUTINT_DB_HOST to point at a "
                "server you manage yourself." % path)
        os.chmod(path, 0o700)
        return path

    # -- binaries ----------------------------------------------------------------------

    def bin(self, name):
        return os.path.join(self.prefix, 'bin', name)

    def provision(self, micromamba):
        """Install the server, if it is not there. `micromamba` is a path to the binary."""
        if os.path.isfile(self.bin('pg_ctl')):
            return
        print("Installing PostgreSQL (%s)..." % SPEC)
        action = 'install' if os.path.isdir(os.path.join(self.prefix, 'conda-meta')) else 'create'
        subprocess.check_call([micromamba, action, '-y', '-p', self.prefix,
                               '-c', 'conda-forge', SPEC])

    # -- versions ----------------------------------------------------------------------

    def installed_major(self):
        out = subprocess.check_output([self.bin('postgres'), '-V']).decode('utf-8')
        match = re.search(r'(\d+)', out)
        return match.group(1) if match else None

    def cluster_major(self):
        try:
            with open(os.path.join(self.data, 'PG_VERSION')) as handle:
                return handle.read().strip().split('.')[0]
        except (IOError, OSError):
            return None

    def check_version(self):
        """Refuse a mismatch in words, rather than letting the postmaster fail into its log."""
        cluster = self.cluster_major()
        if cluster is None:
            return
        installed = self.installed_major()
        if installed and cluster != installed:
            raise PostgresError(
                "This checkout's database was created by PostgreSQL %s and the installed "
                "server is %s, which cannot read it. Either reinstall PostgreSQL %s, or run "
                "`db reset` to start again from an empty database -- which discards "
                "everything in it." % (cluster, installed, cluster))

    # -- state -------------------------------------------------------------------------

    def is_initialized(self):
        return os.path.isfile(os.path.join(self.data, 'PG_VERSION'))

    def postmaster_pid(self):
        try:
            with open(os.path.join(self.data, 'postmaster.pid')) as handle:
                return int(handle.readline().strip())
        except (IOError, OSError, ValueError):
            return None

    def is_running(self):
        """A pid file plus a live process. Deliberately no subprocess: this runs on every
        invocation of every command, and `pg_ctl status` is a process spawn to learn what one
        `kill(pid, 0)` already answers."""
        pid = self.postmaster_pid()
        if pid is None:
            return False
        return _alive(pid)

    # -- ownership ---------------------------------------------------------------------

    def owner(self):
        try:
            with open(self.owner_file) as handle:
                return int(handle.read().strip())
        except (IOError, OSError, ValueError):
            return None

    def claim(self):
        with open(self.owner_file, 'w') as handle:
            handle.write(str(os.getpid()))

    def disown(self):
        try:
            os.remove(self.owner_file)
        except OSError:
            pass

    def owned_by_me(self):
        return self.owner() == os.getpid()

    # -- lifecycle ---------------------------------------------------------------------

    def initdb(self):
        os.makedirs(os.path.dirname(self.data), exist_ok=True)
        print("Creating the database cluster (this happens once)...")
        subprocess.check_call([
            self.bin('initdb'), '-D', self.data, '-U', USER,
            '--encoding=UTF8',
            # C, not the machine's locale. It makes text sort byte-wise, which is what SQLite
            # did, so moving backends does not silently reorder every listing; it does not
            # depend on locales being present inside a conda prefix, which is a real failure on
            # minimal Linux images; and it makes ordering reproducible across machines.
            '--locale=C',
            # Safe only because nothing listens on TCP -- see `start`, which passes -h ''.
            # The only door is a socket in a 0700 directory owned by this user.
            '--auth=trust',
        ], stdout=subprocess.DEVNULL)

    def start(self, own):
        """Start the server. `own` records that this process is responsible for stopping it."""
        socket_dir = self._make_socket_dir()
        subprocess.check_call([
            self.bin('pg_ctl'), '-D', self.data, '-l', self.log,
            # Socket only: no port to collide with another checkout's, nothing on the network.
            '-o', "-k %s -h ''" % socket_dir,
            # -w, or the next thing to connect races the postmaster's startup.
            '-w', '-t', '30', 'start',
        ], stdout=subprocess.DEVNULL)
        if own:
            self.claim()

    def stop(self):
        if not self.is_running():
            self.disown()
            return False
        subprocess.check_call([self.bin('pg_ctl'), '-D', self.data, '-m', 'fast', 'stop'],
                              stdout=subprocess.DEVNULL)
        self.disown()
        return True

    def stop_if_owned(self):
        """The shutdown hook's whole job. Silent, and never raises: this runs at exit, where
        an exception is noise on top of whatever the command was actually doing."""
        try:
            if self.owned_by_me():
                self.stop()
        except Exception:
            pass

    def stop_if_owner_is(self, pid):
        """`stop_if_owned` asked on somebody else's behalf, for the deadman supervisor.

        The supervisor watches a pipe whose write end only `./mutint start` holds, so it learns
        that its parent died however it died -- including `kill -9`, which `atexit` cannot
        reach. It then has to stop the cluster that parent owned, and it cannot use
        `owned_by_me`: its own pid is not the owner's. See `mutint_jobs/supervisor.py`.

        Three things make it safe rather than merely plausible, and each guards a state that
        really occurs:

        - **The lock.** `ensure` holds it across its whole check-start-adopt sequence, so
          without taking it here there is a genuine race: another invocation reads the owner
          file, finds the pid dead and claims, while we -- having read the same file a moment
          earlier -- stop the cluster underneath it, leaving the adopter holding a server it
          believes is running.
        - **The owner check.** An *unowned* cluster must be left alone: `./mutint db start`
          creates one deliberately so a server outlives one command, and its owner file is
          absent, so `owner()` answers None and never equals a pid.
        - **The liveness check.** If the number has since been handed to a live process, this
          is not our parent and the cluster is not ours to stop. Pid reuse is unlikely and
          costs a running database when it happens.

        Never raises, for `stop_if_owned`'s reason: it runs where there is nobody to tell.
        """
        try:
            if pid is None:
                return
            with _FileLock(self.lock_file):
                if self.owner() == pid and not _alive(pid):
                    self.stop()
        except Exception:
            pass

    # -- the database ------------------------------------------------------------------

    def _psql(self, *args):
        return subprocess.check_output(
            [self.bin('psql'), '-h', self.socket_dir, '-U', USER, '-d', 'postgres',
             '-tAc'] + list(args)).decode('utf-8').strip()

    def database_exists(self):
        answer = self._psql("SELECT 1 FROM pg_database WHERE datname = '%s'" % self.name)
        return answer == '1'

    def create_database(self):
        if self.database_exists():
            return False
        subprocess.check_call([self.bin('createdb'), '-h', self.socket_dir, '-U', USER,
                               self.name])
        return True

    # -- the whole of it ---------------------------------------------------------------

    def ensure(self, micromamba, own=True):
        """Provision, initialize, start and create, doing only what is not already done.

        Held under a lock for the whole of it, and the running check is repeated *inside* the
        lock: two terminals starting at once is ordinary, and without that both would decide
        the server was down and one would fail.
        """
        with _FileLock(self.lock_file):
            self.provision(micromamba)
            if not self.is_initialized():
                self.initdb()
            self.check_version()
            if not self.is_running():
                self.start(own=own)
            elif own and self.owner() is not None and not _alive(self.owner()):
                # Its owner is gone -- killed, or crashed before it could stop the server.
                # Adopt it rather than refusing to run.
                self.claim()
            self.create_database()

    def env(self):
        """What the entry script exports so settings can find the server."""
        return {
            'MUTINT_DB_HOST': self.socket_dir,
            'MUTINT_DB_PORT': PORT,
            'MUTINT_DB_NAME': self.name,
            'MUTINT_DB_USER': USER,
            'MUTINT_DB_MANAGED': '1',
        }

    def describe(self):
        """`db status`, as lines. Answers on a tree where nothing is provisioned yet, which
        is what makes it usable as a first command."""
        lines = ["database:  %s" % self.name]
        if is_external():
            lines.append("mode:      external (MUTINT_DB_HOST is set; nothing is managed here)")
            lines.append("host:      %s" % os.environ['MUTINT_DB_HOST'])
            return lines
        lines.append("mode:      managed")
        lines.append("socket:    %s" % self.socket_dir)
        if not os.path.isfile(self.bin('pg_ctl')):
            lines.append("server:    not installed")
            return lines
        lines.append("server:    PostgreSQL %s" % self.installed_major())
        if not self.is_initialized():
            lines.append("cluster:   not created")
            return lines
        lines.append("cluster:   PostgreSQL %s at %s" % (self.cluster_major(), self.data))
        if self.is_running():
            owner = self.owner()
            if owner is None:
                held = "unowned, so it stays up"
            elif owner == os.getpid():
                held = "owned by this process"
            elif _alive(owner):
                held = "owned by pid %d, which stops it when it ends" % owner
            else:
                held = "owner pid %d is gone; the next command adopts it" % owner
            lines.append("running:   yes, pid %s (%s)" % (self.postmaster_pid(), held))
        else:
            lines.append("running:   no")
        return lines


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


class _FileLock(object):
    """An exclusive lock held for the whole of `ensure`.

    `fcntl.flock`, which is stdlib and works on every platform the entry script knows how to
    download micromamba for. Released by the close, and by the process dying, so a killed
    command cannot leave the lock held.
    """

    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.handle = open(self.path, 'w')
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()
        return False

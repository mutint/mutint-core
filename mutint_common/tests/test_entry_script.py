"""The entry script, run for real against a scratch project.

The script is three byte-identical copies -- `mutint-core/mutint`, `mutint/mutint` and
`aledb/aledb` -- and runs before any venv or Django exists, so nothing else in the suite can
see what it does. These tests copy mutint-core's copy into a temp directory beside stub
components and a fake micromamba, run it as a subprocess, and read what it asked micromamba
to install.

**What they pin is the order of two things on `./mutint start`**: a staged update is applied,
and *then* the component list is read. An update can add a component -- mutint-fastqc arrived
that way -- and the tools and requirements sentinels hash only the components the process
knows about. Read before the update, the new component's `tools.txt` was invisible, the
sentinel still matched, and its tool was never installed; the next launch, reading
`.gitmodules` afresh, would have installed it.

Nothing here starts a database or builds a venv: the stub `pg` says the server is external,
the venv's sentinel is pre-written to match, and `env/main/bin/python` is a script that exits.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap

from django.test import SimpleTestCase

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "mutint")

# Must equal the entry script's `_PYTHON_SPEC`: the venv sentinel is its digest when no
# component has a requirements.txt, which is how these tests keep the script from building one.
PYTHON_SPEC = "python=3.13.*"

GITMODULE = '[submodule "{name}"]\n\tpath = {name}\n\turl = ../{name}\n\tbranch = main\n'

STUB_PG = "def is_external():\n    return True\n"

# `apply_staged` adds a component, as an update that brings a new plugin does: a new
# .gitmodules entry and a checked-out directory holding a tools.txt.
STUB_UPDATE = textwrap.dedent('''\
    import os

    def apply_staged(base_dir, pg=None):
        with open(os.path.join(base_dir, ".gitmodules"), "a") as handle:
            handle.write({entry!r})
        os.makedirs(os.path.join(base_dir, "plugin"), exist_ok=True)
        with open(os.path.join(base_dir, "plugin", "tools.txt"), "w") as handle:
            handle.write("newpkg\\n")
''').format(entry=GITMODULE.format(name="plugin"))

# Records its argv, and creates the prefix after `-p` as the real one does -- the script writes
# the tools sentinel into it afterwards.
FAKE_MICROMAMBA = textwrap.dedent('''\
    #!/bin/sh
    printf '%s\\n' "$*" >> "{record}"
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "-p" ]; then mkdir -p "$2"; fi
        shift
    done
''')


def _executable(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
    os.chmod(path, stat.S_IRWXU)


class StagedUpdateTestCase(SimpleTestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.record = os.path.join(self.root, "micromamba-calls.txt")

        shutil.copy(SCRIPT, os.path.join(self.root, "mutint"))
        with open(os.path.join(self.root, ".gitmodules"), "w") as handle:
            handle.write(GITMODULE.format(name="core"))
        common = os.path.join(self.root, "core", "mutint_common")
        os.makedirs(common)
        with open(os.path.join(common, "pg.py"), "w") as handle:
            handle.write(STUB_PG)
        with open(os.path.join(common, "update.py"), "w") as handle:
            handle.write(STUB_UPDATE)
        with open(os.path.join(self.root, "core", "tools.txt"), "w") as handle:
            handle.write("oldpkg\n")

        _executable(os.path.join(self.root, "env", "micromamba", "bin", "micromamba"),
                    FAKE_MICROMAMBA.format(record=self.record))
        _executable(os.path.join(self.root, "env", "main", "bin", "python"),
                    "#!/bin/sh\nexit 0\n")
        with open(os.path.join(self.root, "env", "main", ".installed"), "w") as handle:
            handle.write(hashlib.sha256(PYTHON_SPEC.encode("utf-8")).hexdigest())

    def _stage(self):
        os.makedirs(os.path.join(self.root, "data"), exist_ok=True)
        with open(os.path.join(self.root, "data", "update.json"), "w") as handle:
            json.dump({"staged": {"ref": "v9.9.9"}}, handle)

    def _start(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("MUTINT_")}
        completed = subprocess.run(
            [sys.executable, os.path.join(self.root, "mutint"), "start"],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return completed

    def _installed(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record) as handle:
            return [line.split() for line in handle if line.strip()]

    def test_a_plain_start_installs_what_the_components_list(self):
        """The harness itself: no update, the one component's tool."""
        self._start()
        calls = self._installed()
        self.assertEqual(1, len(calls), calls)
        self.assertIn("oldpkg", calls[0])
        self.assertNotIn("newpkg", calls[0])

    def test_a_component_an_update_adds_has_its_tools_installed_on_that_launch(self):
        self._stage()
        self._start()
        calls = self._installed()
        self.assertEqual(1, len(calls), calls)
        self.assertIn("newpkg", calls[0], "the new component's tools.txt was not read")
        self.assertIn("oldpkg", calls[0])

    def test_the_three_copies_of_the_script_agree(self):
        """Where the suite is checked out beside this repo, the assembled projects' copies
        must be this one byte for byte: they have no tests of their own."""
        suite = os.path.dirname(os.path.dirname(SCRIPT))
        with open(SCRIPT, "rb") as handle:
            ours = handle.read()
        for copy in (os.path.join(suite, "mutint", "mutint"),
                     os.path.join(suite, "aledb", "aledb")):
            if not os.path.isfile(copy):
                continue
            with open(copy, "rb") as handle:
                self.assertEqual(ours, handle.read(), "%s has drifted" % copy)

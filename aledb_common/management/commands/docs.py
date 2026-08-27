"""Build the plugin API documentation, or serve it with live reload.

    ./aledb docs             build to site/
    ./aledb docs --serve     serve at http://127.0.0.1:8001 and rebuild as you type
    ./aledb docs --strict    fail on a broken link or an unresolvable reference

The toolchain is deliberately absent from `requirements.txt` -- the entry script installs that
into every deployment, and a production ALEdb has no use for a static site generator. So this
installs `requirements-docs.txt` on first use rather than failing with an ImportError and a
suggestion, which is the same bargain `./aledb start` makes when it bootstraps the venv.
"""

import os
import subprocess
import sys

from django.core.management import BaseCommand, CommandError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
REQUIREMENTS = os.path.join(BASE_DIR, "requirements-docs.txt")
CONFIG = os.path.join(BASE_DIR, "mkdocs.yml")


class Command(BaseCommand):

    help = "Build the plugin API documentation (docs/ -> site/)."

    def add_arguments(self, parser):
        parser.add_argument("--serve", action="store_true",
                            help="serve with live reload instead of building")
        parser.add_argument("--port", type=int, default=8001,
                            help="port for --serve; 8001 so it does not collide with "
                                 "./aledb start on 8000")
        parser.add_argument("--strict", action="store_true",
                            help="treat a broken link or unresolved reference as an error")

    def handle(self, *args, **options):
        if not os.path.isfile(CONFIG):
            raise CommandError("no mkdocs.yml beside %s" % BASE_DIR)

        self._refuse_if_embedded()
        self._ensure_toolchain()

        command = [sys.executable, "-m", "mkdocs",
                   "serve" if options["serve"] else "build",
                   "--config-file", CONFIG]
        if options["strict"]:
            command.append("--strict")
        if options["serve"]:
            command += ["--dev-addr", "127.0.0.1:%d" % options["port"]]
            self.stdout.write("Serving on http://127.0.0.1:%d -- ctrl-c to stop"
                              % options["port"])

        try:
            # `call`, not `check_call`: mkdocs prints its own diagnostics, and a traceback
            # from CalledProcessError on top of them says nothing extra.
            code = subprocess.call(command, cwd=BASE_DIR)
        except KeyboardInterrupt:
            return
        if code != 0:
            raise CommandError("mkdocs exited %d" % code)
        if not options["serve"]:
            self.stdout.write(self.style.SUCCESS(
                "Built %s" % os.path.join(BASE_DIR, "site", "index.html")))

    def _refuse_if_embedded(self):
        """Refuse to build when aledb-core is a submodule of the project being run.

        Every command in this repo is inherited by an assembled project -- both entry scripts
        end at `aledb_common.cli.manage()` -- so `./mutint docs` reaches here. `BASE_DIR` is
        derived from this file, so there it resolves to the *submodule*, and the build would
        write `mutint/aledb-core/site/` from a detached-HEAD checkout: a second copy of a site
        whose sources live somewhere else, going stale the moment the pointer moves.

        `ALEDB_TOOLS_DIR` is what distinguishes the two. The entry script exports it before it
        re-execs and is the one place that knows the project root -- settings cannot, because
        an assembled project reaches `get_base_settings()` through aledb-core's
        `config/defaults.py`. Unset means nothing was exported, which means this was not run
        through an entry script at all; then there is nothing to compare against and refusing
        would be a guess.
        """
        tools_dir = os.environ.get("ALEDB_TOOLS_DIR")
        if not tools_dir:
            return

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(tools_dir)))
        if os.path.normpath(project_root) == os.path.normpath(BASE_DIR):
            return

        # Only call it a submodule if it is actually inside the project. Usually it is; it is
        # not when someone has shadowed the submodule with another checkout on PYTHONPATH,
        # and asserting a detached HEAD about a path that has not been looked at is the kind
        # of confident-and-wrong message that costs an afternoon.
        embedded = os.path.normpath(BASE_DIR).startswith(
            os.path.normpath(project_root) + os.sep)
        if embedded:
            what = ("here aledb-core is a submodule of %s rather than the project itself.\n\n"
                    "Building would write into\n    %s/site\n"
                    "which is a detached-HEAD checkout, so the output would be a second copy "
                    "of a site whose sources live in your own aledb-core clone -- going stale "
                    "the moment the submodule pointer moved."
                    % (project_root, BASE_DIR))
        else:
            what = ("the project being run is %s, and this command came from a different "
                    "checkout:\n    %s\n\n"
                    "Building would write that checkout's site from this project's "
                    "environment, which is not a combination anybody means to ask for."
                    % (project_root, BASE_DIR))

        raise CommandError(
            "This builds aledb-core's documentation, and %s\n\n"
            "Run it from the aledb-core repository instead:\n"
            "    cd /path/to/aledb-core && ./aledb docs\n\n"
            "The documentation is the same either way -- it describes aledb-core's plugin "
            "API, which is what %s installs."
            % (what, os.path.basename(project_root)))


    def _ensure_toolchain(self):
        try:
            import mkdocs  # noqa: F401
            import mkdocstrings  # noqa: F401
        except ImportError:
            pass
        else:
            return

        if not os.path.isfile(REQUIREMENTS):
            raise CommandError("mkdocs is not installed and %s is missing" % REQUIREMENTS)
        self.stdout.write("Installing the documentation toolchain (first run)...")
        code = subprocess.call([sys.executable, "-m", "pip", "install",
                                "-r", REQUIREMENTS])
        if code != 0:
            raise CommandError(
                "could not install %s. Install it by hand and try again." % REQUIREMENTS)

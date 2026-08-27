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

"""Build this project's manual, or serve it with live reload.

    ./mutint docs             build to site/
    ./mutint docs --serve     serve at http://127.0.0.1:8001 and rebuild as you type
    ./mutint docs --strict    fail on a broken link or an unresolvable reference

Run from mutint-core it builds mutint-core's documentation. Run from an assembled project --
every command here is inherited, because both entry scripts end at `mutint_common.cli.manage()`
-- it builds *that project's* manual: the project's own pages plus every installed component's,
merged by audience. `mutint_common.docs_manual` does the collecting and explains the rules.

There is one code path. Standalone, the project has no other components and the merge is a
merge of one.

The toolchain is deliberately absent from `requirements.txt` -- the entry script installs that
into every deployment, and a production MutInt has no use for a static site generator. So this
installs `requirements-docs.txt` on first use rather than failing with an ImportError and a
suggestion, which is the same bargain `./mutint start` makes when it bootstraps the venv.
"""

import os
import subprocess
import sys

from django.core.management import BaseCommand, CommandError

from mutint_common import docs_manual

#: mutint-core's own directory. Only used as a fallback: when no entry script exported
#: `MUTINT_TOOLS_DIR` there is nothing else that knows what the project is.
CORE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
REQUIREMENTS = os.path.join(CORE_DIR, "requirements-docs.txt")


class Command(BaseCommand):

    help = "Build this project's documentation (docs/ -> site/)."

    def add_arguments(self, parser):
        parser.add_argument("--serve", action="store_true",
                            help="serve with live reload instead of building")
        parser.add_argument("--port", type=int, default=8001,
                            help="port for --serve; 8001 so it does not collide with "
                                 "./mutint start on 8000")
        parser.add_argument("--strict", action="store_true",
                            help="treat a broken link or unresolved reference as an error")

    def handle(self, *args, **options):
        root = docs_manual.project_root() or CORE_DIR
        if not os.path.isdir(os.path.join(root, "docs")):
            raise CommandError(
                "%s has no docs/ directory, so there is nothing of its own to build.\n"
                "A project's manual starts with its own pages; its components' are merged "
                "into them." % root)
        if not os.path.isfile(os.path.join(root, "mkdocs.yml")):
            raise CommandError("%s has a docs/ directory but no mkdocs.yml beside it." % root)

        self._ensure_toolchain()

        components = docs_manual.contributing_components(root)
        config_path = docs_manual.assemble(root, components)
        if components:
            self.stdout.write("Collected %d component(s): %s"
                              % (len(components), ", ".join(n for n, _ in components)))

        command = [sys.executable, "-m", "mkdocs",
                   "serve" if options["serve"] else "build",
                   "--config-file", config_path]
        if options["strict"]:
            command.append("--strict")
        if options["serve"]:
            command += ["--dev-addr", "127.0.0.1:%d" % options["port"]]
            self.stdout.write("Serving on http://127.0.0.1:%d -- ctrl-c to stop"
                              % options["port"])

        try:
            # `call`, not `check_call`: mkdocs prints its own diagnostics, and a traceback
            # from CalledProcessError on top of them says nothing extra.
            code = subprocess.call(command, cwd=os.path.dirname(config_path))
        except KeyboardInterrupt:
            return
        if code != 0:
            raise CommandError("mkdocs exited %d" % code)
        if not options["serve"]:
            self.stdout.write(self.style.SUCCESS(
                "Built %s" % os.path.join(root, "site", "index.html")))

    def _ensure_toolchain(self):
        try:
            import mkdocs  # noqa: F401
            import mkdocstrings  # noqa: F401
            import yaml  # noqa: F401
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

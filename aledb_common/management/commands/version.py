"""`./aledb version` -- print the versions in play, or bump one.

aledb-core's own lives in aledb_common/version.py. An assembled project versions
itself the same way: put a version.py exposing __version__ in one of its apps and
it is discovered here, so `./mutint version` reports MutInt's alongside the core
it runs on. Bumping is deliberate and paired with a `v<version>` git tag; this
command only rewrites the file, because these repos do not commit on their own.
"""
import importlib
import re

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from aledb_common import version as version_module

PARTS = ("major", "minor", "patch")
CORE = "aledb-core"
_ASSIGNMENT = re.compile(r'^(__version__\s*=\s*)"([^"]+)"', re.MULTILINE)


def bump(current, part):
    """Return `current` with `part` incremented and everything after it zeroed."""
    try:
        numbers = [int(piece) for piece in current.split(".")]
    except ValueError:
        raise CommandError(
            'cannot bump "%s": expected a numeric MAJOR.MINOR.PATCH' % current)
    if len(numbers) != 3:
        raise CommandError(
            'cannot bump "%s": expected three parts, got %d' % (current, len(numbers)))
    index = PARTS.index(part)
    numbers[index] += 1
    for later in range(index + 1, len(numbers)):
        numbers[later] = 0
    return ".".join(str(number) for number in numbers)


def rewrite(path, new_version):
    """Replace the __version__ assignment in `path`, leaving the rest untouched."""
    with open(path) as handle:
        source = handle.read()
    replaced, count = _ASSIGNMENT.subn(
        lambda match: '%s"%s"' % (match.group(1), new_version), source, count=1)
    if count != 1:
        raise CommandError("no __version__ assignment found in %s" % path)
    with open(path, "w") as handle:
        handle.write(replaced)


def components():
    """Every versioned component, the assembled project's first.

    aledb-core always has one. An installed app contributes one by exposing
    `__version__` from a `version` submodule -- no registration, because the app
    being installed is already the statement that it is part of this project.
    """
    found = []
    for config in apps.get_app_configs():
        if config.name == "aledb_common":
            continue
        try:
            module = importlib.import_module("%s.version" % config.name)
        except ImportError:
            continue
        version = getattr(module, "__version__", None)
        if version:
            found.append((getattr(module, "NAME", config.label), module, version))
    return found + [(CORE, version_module, version_module.__version__)]


class Command(BaseCommand):
    help = "Print the versions in play, or bump one with --bump."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bump", choices=PARTS,
            help="increment this part of a version and rewrite its version.py")
        parser.add_argument(
            "--component",
            help="which component to bump; only needed when more than one exists")

    def handle(self, *args, **options):
        found = components()
        part = options.get("bump")
        if not part:
            for name, _module, version in found:
                self.stdout.write("%s %s" % (name, version))
            return

        wanted = options.get("component")
        if wanted:
            matched = [c for c in found if c[0] == wanted]
            if not matched:
                raise CommandError("no such component: %s (have %s)"
                                   % (wanted, ", ".join(c[0] for c in found)))
        elif len(found) == 1:
            matched = found
        else:
            # Refuse to guess: bumping the wrong repo's version is quiet and
            # the mistake only surfaces at release.
            raise CommandError(
                "--component is required, one of: %s" % ", ".join(c[0] for c in found))

        name, module, current = matched[0]
        new_version = bump(current, part)
        rewrite(module.__file__, new_version)
        self.stdout.write("%s %s -> %s" % (name, current, new_version))
        self.stdout.write("Tag the release commit: git tag v%s" % new_version)

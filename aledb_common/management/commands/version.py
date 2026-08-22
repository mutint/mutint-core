"""`./aledb version` -- print aledb-core's version, or bump it.

The version lives in aledb_common/version.py and is bumped deliberately, paired
with a `v<version>` git tag on the release commit. This command only rewrites the
file; tagging and committing are left to the release step, because this repo does
not commit on its own.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from aledb_common import version as version_module

PARTS = ("major", "minor", "patch")
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


class Command(BaseCommand):
    help = "Print aledb-core's version, or bump it with --bump."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bump", choices=PARTS,
            help="increment this part of the version and rewrite version.py")

    def handle(self, *args, **options):
        current = version_module.__version__
        part = options.get("bump")
        if not part:
            self.stdout.write("aledb-core %s" % current)
            return

        new_version = bump(current, part)
        rewrite(version_module.__file__, new_version)
        self.stdout.write("%s -> %s" % (current, new_version))
        self.stdout.write("Tag the release commit: git tag v%s" % new_version)

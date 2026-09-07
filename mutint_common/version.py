"""The version of mutint-core itself.

This is the single source of truth for *mutint-core*, and only for it. Every
plugin carries its own version.py now, and a deployment names and versions itself
through the MUTINT_BRANDING setting -- so this is the platform's number, not the
number of whatever is running it.

Bump it with `./mutint version --bump patch|minor|major`, and tag the release
commit `v<version>` to match. Standalone that is the whole command, mutint-core
being the only component; anywhere a plugin is installed the command refuses to
guess and wants `--component mutint-core` too.
"""

__version__ = "0.0.1"


def get_version():
    return __version__

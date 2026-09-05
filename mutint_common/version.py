"""The version of mutint-core itself.

This is the single source of truth. It is the platform's own version, not the
version of whatever deployment is running it -- a deployment names itself through
the MUTINT_BRANDING setting and may version itself independently.

Bump it with `./mutint version --bump patch|minor|major`, and tag the release
commit `v<version>` to match.
"""

__version__ = "1.1.0"


def get_version():
    return __version__

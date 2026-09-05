"""Finding the external binaries that components declare in their tools.txt.

The suite had no notion of an external tool before this: the two existing subprocess calls
(`mutint_common.util._git`, the browser-opening `open` in the start command) both use a bare
name and shrug when it is missing, because nothing depends on the result. A tool that has to
actually run needs more than that -- somewhere definite to look, and an error that says what
to do.

Where to look is ``MUTINT_TOOLS_DIR``, which the entry script exports before it re-execs. It is
not derived from settings on purpose: an assembled project reaches ``get_base_settings()``
through mutint-core's ``config/defaults.py``, which passes the *mutint-core* directory as
``base_dir``, so anything derived there points inside the submodule -- the same trap
``templates/`` and ``staticfiles/`` have to work around. The entry script is the one place
that knows the project root for certain.

PATH is still consulted as a fallback, so a developer with their own bedtools does not have to
wait for a solve, and so a deployment that installs tools by other means keeps working.
"""

import os
import shutil

from django.conf import settings


class ToolMissing(RuntimeError):
    """A required external tool is installed nowhere this knows to look."""


def tools_dir():
    """The managed tools environment, or None when there is no such setting."""
    return getattr(settings, "MUTINT_TOOLS_DIR", None)


def tool_path(name):
    """Absolute path to `name`, or None.

    The managed environment wins over PATH, so a project that installed a pinned version gets
    that one rather than whatever happens to be on the developer's PATH.
    """
    directory = tools_dir()
    if directory:
        candidate = os.path.join(directory, "bin", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name)


def require(name):
    """`tool_path(name)`, or raise ToolMissing naming the command that installs it."""
    found = tool_path(name)
    if found:
        return found
    raise ToolMissing(
        "%s is not installed. It is declared in a component's tools.txt; run "
        "`./mutint install` (or `./mutint install`) to build env/tools, or put %s on PATH."
        % (name, name))


def have(*names):
    """Whether every one of `names` can be found -- for deciding to skip optional work."""
    return all(tool_path(name) for name in names)

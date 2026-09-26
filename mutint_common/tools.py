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


#: Where bioconda's `openjdk` puts the JVM inside a prefix. Nothing of it is in `bin/`.
JVM_DIR = os.path.join("lib", "jvm")


def tool_environment(env=None):
    """`env` with the managed tools directory -- and its JVM, if it has one -- first on PATH.

    For a tool that runs other tools by bare name: breseq's bowtie2, ISEScan's hmmer, a Java
    wrapper's `java`. Prepending `<tools>/bin` is the whole of it for most packages.

    **Not for a JVM.** bioconda's `openjdk` puts nothing in the prefix's `bin/`: the JVM is at
    `lib/jvm/bin/java` and conda exports `JAVA_HOME` from an activation script, which
    `mutint_jobs.processes.run_tool` does not run. So a Java tool -- bbmap's `sendsketch.sh`,
    FastQC -- would otherwise run on whatever `java` the *host* has, which on a developer Mac
    is `/usr/bin/java` and on a clean machine is nothing. The JVM's `bin` goes on PATH ahead of
    the host's and `JAVA_HOME` is set to what `openjdk_activate.sh` would set, gated on the
    directory existing so a prefix without a JVM leaves the host's own alone.

    mutint-isescan, mutint-breseq and mutint-refsniff each carry a copy of this from before it
    was here; they can call this one instead.
    """
    env = dict(os.environ if env is None else env)
    directory = tools_dir()
    if not directory:
        return env
    entries = [os.path.join(directory, "bin")]
    jvm = os.path.join(directory, JVM_DIR)
    if os.path.isdir(os.path.join(jvm, "bin")):
        entries.append(os.path.join(jvm, "bin"))
        env["JAVA_HOME"] = jvm
        env["JAVA_LD_LIBRARY_PATH"] = os.path.join(jvm, "lib", "server")
    existing = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(entries + ([existing] if existing else []))
    return env

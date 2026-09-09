"""A stand-in for whatever a job shells out to, so `processes.run_tool` can be tested.

**A real executable on disk rather than a patched `subprocess`**, the same choice
`mutint_breseq.tests.fake_breseq` makes and for a sharper reason here: what these tests are
about is a *process group* -- that signalling the parent reaches the children it started -- and
a mock has no children to leave behind. Nothing about the failure this guards against is
visible to a test that never starts a process.

It prints to both streams, so the merge of stdout and stderr into one log can be asserted on
too, and optionally spawns a child and sleeps so there is something to cancel.
"""

import os
import stat
import textwrap

SCRIPT = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for a tool a job runs. Prints, and optionally sleeps with a child."""
    import json
    import os
    import subprocess
    import sys
    import time

    sys.stdout.write("out: %s\\n" % " ".join(sys.argv[1:]))
    sys.stdout.flush()
    # To stderr, because run_tool merges the two and the order has to survive it.
    sys.stderr.write("err: something on the other stream\\n")
    sys.stderr.flush()

    if os.environ.get("FAKE_TOOL_FAIL"):
        sys.exit(int(os.environ["FAKE_TOOL_FAIL"]))

    if os.environ.get("FAKE_TOOL_SLEEP"):
        # A child of its own, so a test can assert the signal reached the whole group. It
        # sleeps in a subprocess rather than a thread: a thread dies with its parent, which
        # is exactly the failure being tested for.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open(os.environ["FAKE_TOOL_PIDS"], "w") as handle:
            json.dump({"parent": os.getpid(), "child": child.pid}, handle)
        time.sleep(600)

    sys.stdout.write("done\\n")
''')


def install(directory, name="faketool"):
    """Write the fake into ``<directory>/<name>`` and return its path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        handle.write(SCRIPT)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path

"""Opening the reader's browser at the server, on whatever this machine is.

`./mutint start` used to run macOS's `open`, and nothing else. On Linux that is either
absent -- a `FileNotFoundError` in the Timer thread -- or Debian's `openvt` alias, which fails
trying to grab a virtual console; with the output captured, neither said anything. Under WSL
the right browser is the *Windows* one, reached through `wslview` (from wslu, on the Ubuntu
images) or PowerShell's `Start-Process` over the interop PATH, which is why WSL is a branch of
its own rather than left to `xdg-open`: without wslu that command has nothing to open.

**Not the standard library's `webbrowser`**, deliberately. With no `DISPLAY` it falls back to
console browsers -- `www-browser`, `lynx`, `w3m` -- and would start one *in the terminal that
is running the server*. A headless box gets None here, and `start` prints the address instead.

`browser_command` is the decision and `open_browser` the side effect, kept apart so the
decision can be tested on a machine that is none of these.
"""

import os
import shutil
import subprocess
import sys

#: The kernel release string names Microsoft under WSL: `4.4.0-19041-Microsoft` on WSL1,
#: `5.15.90.1-microsoft-standard-WSL2` on WSL2.
OSRELEASE = "/proc/sys/kernel/osrelease"


def is_wsl():
    """Whether this Linux is Windows Subsystem for Linux. Never raises."""
    try:
        with open(OSRELEASE) as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def browser_command(url):
    """The argv that opens `url` in a browser here, or None when there is no way to."""
    if sys.platform == "darwin":
        return ["open", url]
    if sys.platform.startswith("linux") and is_wsl():
        if shutil.which("wslview"):
            return ["wslview", url]
        if shutil.which("powershell.exe"):
            return ["powershell.exe", "-NoProfile", "-Command", "Start-Process", url]
        return None
    if shutil.which("xdg-open") and (os.environ.get("DISPLAY") or
                                     os.environ.get("WAYLAND_DISPLAY")):
        return ["xdg-open", url]
    return None


def open_browser(url):
    """Open `url` in a browser, if there is a way to. Never raises: this runs in a Timer
    thread, where a traceback would be noise over the server's own output."""
    command = browser_command(url)
    if command is None:
        return False
    try:
        subprocess.run(command, capture_output=True)
    except OSError:
        return False
    return True

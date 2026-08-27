# Packaging and installing

## `requirements.txt`

One at your repository root, even if it is empty. The entry script collects every component's
`requirements.txt` in `.gitmodules` order plus the project's own, and installs them into the
shared venv. A component declares what it needs beside its own code; nothing central lists
them.

## `tools.txt` — non-Python tools

If your plugin shells out to something that is not a Python package, declare it in a
`tools.txt` beside your `requirements.txt`, as conda package specs:

```
bedtools
ucsc-bedgraphtobigwig
```

The entry scripts collect these exactly as they collect requirements and install them into
`env/tools` with micromamba, downloaded as a static binary so no conda is needed on the host.
This cannot be a Django registry — installation happens before Django exists.

Find them at runtime through `aledb_common.tools`:

```python
from aledb_common.tools import require, tool_path

path = tool_path("bedtools")      # env/tools/bin, then PATH, then None
path = require("bedtools")        # or raises ToolMissing naming the install command
```

!!! tip "Check the channel before choosing a tool"

    One candidate here would have done a job in a single call and had **no osx-arm64 build**,
    which would have forced an entire `--platform osx-64` environment under Rosetta on Apple
    Silicon. Two tools that build natively were worth the extra step.

## Example datasets

A dataset is a directory of files the import path already understands — not a Django fixture.
Loading one runs the real import handlers, so the derived data it exists to demonstrate is
genuinely computed.

```python
import os
from aledb_common.example_registry import register_example_dataset

register_example_dataset(
    'aledb-yourthing-example',
    os.path.join(os.path.dirname(__file__), 'examples', 'yourthing'),
    description='What this shows.')
```

Ship a `README.md` beside the data stating the expected answer as a table, and assert that
answer in your tests. The reason this exists at all: a plugin's page renders an empty table
when there is nothing to show *and* when the feature is broken, and there was no data anywhere
in the suite that could tell the two apart.

## Adding your plugin to an assembled project

```bash
cd mutint
git -c protocol.file.allow=always submodule add ../aledb-yourthing aledb-yourthing
git add -A && git commit -m "feat: add aledb-yourthing submodule"
```

That is the whole installation. No edit to `config/settings.py` or `config/urls.py`:

- `settings.py` reads `.gitmodules`, puts each submodule directory on `sys.path`, and
  discovers `INSTALLED_APPS` by scanning for packages holding both `__init__.py` and
  `apps.py`.
- Your URLs arrive through `plugin_registry`, your sidebar entry through `nav_registry`.

Your position in the sidebar and in rebuild order follows your position in `.gitmodules`,
because that determines `INSTALLED_APPS` order.

## Keeping the submodule pointer current

An assembled project pins each submodule to a commit, so committing in your plugin does not
change what the project runs until the pointer moves:

```bash
cd mutint
git -c protocol.file.allow=always submodule update --remote aledb-yourthing
git submodule status aledb-yourthing        # must equal your repo's HEAD
./mutint check
git add aledb-yourthing && git commit -m "chore: bump aledb-yourthing"
```

!!! warning "Check the SHA against your repo's HEAD"

    `--remote` follows each submodule's `origin/HEAD`, which is not always the branch being
    developed. This has silently rolled a pointer *backwards* here. If the SHA does not
    match, check out the intended commit in the submodule explicitly.

!!! danger "Never commit inside the submodule copy"

    Your plugin exists twice: your own checkout, and a clone inside the assembled project on
    a **detached HEAD**. A commit made in the second is reachable only by SHA inside that one
    clone and is discarded the next time the pointer moves. `cd aledb-yourthing` from inside
    the project lands there, looks identical, and passes its tests.

    Check `git branch --show-current` before committing: an empty answer means the wrong
    checkout. If it happens, recover with
    `git fetch <path-to-that-clone> HEAD && git merge --ff-only FETCH_HEAD` from your own
    checkout rather than redoing the work.

# Testing a plugin

The single most important thing on this page:

!!! danger "Your tests do not run in aledb-core"

    `./aledb test` has no plugin discovery of any kind. A plugin's suite runs only inside an
    assembled project:

    ```bash
    cd mutint
    ./mutint test aledb_yourthing     # your suite
    ./mutint test                     # everything installed, yours included
    ```

    Running them from aledb-core does not fail — it finds nothing, and reports
    `Ran 0 tests ... OK`.

## Why a bare run needs a custom runner

`./aledb test` works by ordinary `unittest` discovery, because aledb-core's app packages sit
directly under the working directory. In an assembled project they do not: they live inside
submodule directories named `aledb-core`, `aledb-yourthing` and so on. Those names contain
hyphens, so they can never be Python packages and discovery can never descend into them,
however they are laid out. Your app is importable only because settings puts each submodule
directory on `sys.path`.

So a bare run has to be told what to run. `aledb_common.test_runner` substitutes the installed
first-party apps, taken from `about_registry.first_party_app_configs()` — the same predicate
the About page inventories with, so "which apps are ours" is decided once. Explicit labels
still work and take precedence.

## The store is redirected for you

The test runner points `ALEDB_STORE_DIR` at a temporary directory for the duration of a run.

This is a backstop, not a convenience. Store paths are derived from primary keys, and a test
database numbers its experiments from 1 — so a test that exercises the importer and forgets
`override_settings(ALEDB_STORE_DIR=...)` writes its fixtures into `experiments/1/` of whatever
deployment it was run against. It has happened: a run replaced a real REL606 reference with a
6 kb synthetic one, and the genome browser then drew empty tracks for all 29 samples in that
experiment while their BAMs and coverage files sat perfectly intact.

Per-test overrides still work and take precedence. Write them anyway; the runner covers the
tests nobody remembered to write them for.

## Never assert on what is absent from a shared registry

The registries are shared with every installed app, so their contents are a property of the
*deployment*, not of your plugin.

```python
# Wrong. Passes standalone, fails the moment another plugin is installed.
self.assertNotIn("Compare", nav_labels)

# Right.
self.assertIn("Your Thing", nav_labels)
```

Three tests in aledb-core made exactly this mistake. Assert your own registrations; leave
everybody else's to them. Where you genuinely need to count, count per component rather than
in total — the About page's test does this, because the number of sections is the number of
installed components and that is not a fixed number.

## Registering inside a test

If a test needs a rebuild, an import type or a nav entry of its own, register it under a name
of its own and remove it again:

```python
from aledb_common.rebuild_registry import register_rebuilder, unregister_rebuilder

def _register(self, name, fn):
    register_rebuilder(name, fn)
    self.addCleanup(unregister_rebuilder, name)
    return name
```

The registries are module-level state that outlives a test case. A registration left behind
leaks into every later test in the process, and the failure surfaces somewhere else entirely.

## Two traps that have cost real time here

**A missing staleness row already counts as stale.** `is_stale()` answers `True` when nothing
has ever been recorded, so a test that asserts derived data *is* stale passes on a clean
database whatever the code does — and one that asserts it is *fresh* fails for a reason
unrelated to the change. Rebuild first, assert freshness, then act:

```python
run_rebuilds(experiment.ale_id, force=True)
self.assertFalse(is_stale("yourthing", experiment.ale_id))   # a real starting point
```

**Subclassing a `TestCase` re-runs every test it declares.** Inheriting from a class that
carries both a fixture and tests silently doubles them. Give the second class its own `setUp`,
or put the new tests in the existing class. Both mistakes have been made in this codebase, and
the symptom is a suite that grows by more tests than you wrote.

## Fixtures

Building an experiment from models is faster and clearer than driving an import, and every
plugin's tests do it. The chain is
`AleId → Flask → Isolate → TechnicalReplicate → ResequencingExperiment`; mutations are
`Mutation` rows joined to samples by `ObservedMutation`.

Two things that are easy to miss:

- **Create the project through the view**, `POST /ale/projects/create/`, rather than
  `Project.objects.create()`. Access is granted on the project, and an experiment whose
  project nobody owns can be viewed by nobody — a page test then fails with a permission
  error rather than anything to do with your plugin.
- **`Mutation.gene` must not be null** if your test renders any of core's mutation tables. The
  builder hands it to a splitter unguarded. The column is nullable, so this is a trap rather
  than a rule.

## What to test

The things worth covering in a plugin, in rough order of how often they break:

1. **Your registrations happened.** That your nav entry exists, your URL reverses, your export
   type is offered. These are one-line tests and they catch a `ready()` that stopped running.
2. **Your derived data is recomputed when the mutations change**, and — if you registered
   `auto=False` — that your page notices when it has not been.
3. **Permission.** That a reader cannot reach your write endpoints and that a locked
   experiment refuses them. See [URLs, views and permissions](views-and-urls.md).
4. **The computation itself**, against a fixture small enough to reason about by hand.

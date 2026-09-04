# Testing a plugin

The single most important thing on this page:

!!! danger "Your plugin's tests run only in an assembled project"

    ```bash
    cd mutint
    ./mutint test aledb_yourthing     # your suite
    ./mutint test                     # everything installed, yours included
    ```

    Not because of anything about testing — because your app is not installed in aledb-core.
    It lives in a different repository, so it is not on that project's `sys.path` and not in
    its `INSTALLED_APPS`. `./aledb test aledb_yourthing` therefore errors on an app label it
    has never heard of.

    aledb-core runs **its own** suite normally, and with the same runner; there is nothing
    second-class about either mode. What it cannot do is run tests for code that is not
    installed in it, and teaching it to would mean core knowing which plugins exist — the one
    thing this architecture is built to avoid.

## One runner, both modes

There is no separate standalone path. `TEST_RUNNER` is set once, in
`aledb_common/base_settings.py`, and aledb-core and every assembled project inherit it:

```python
'TEST_RUNNER': 'aledb_common.test_runner.AledbTestRunner'
```

Given no labels, it runs the **installed first-party apps** rather than whatever discovery
finds. aledb-core has sixteen of those; an assembled project has those sixteen plus each
installed plugin's. That is the whole difference between the two.

## Why the runner substitutes at all

Standalone, it need not. aledb-core's app packages sit directly under the working directory, so
ordinary `unittest` discovery would find exactly the same set — substituting changes nothing
there.

In an assembled project it is the difference between running the suite and not. The code lives
in submodule directories named `aledb-core`, `aledb-yourthing` and so on, and **those names
contain hyphens**, so they can never be Python packages and discovery can never descend into
them, however they are laid out. Your app is importable only because settings puts each
submodule directory on `sys.path`, which discovery does not consult.

Renaming the directories would not help either: giving a submodule root an `__init__.py` would
make every app importable by two dotted paths at once, and `aledb_yourthing.models` and
`aledb_core.aledb_yourthing.models` are two module objects holding two sets of model classes.

So a bare run is told what to run instead, from
`about_registry.first_party_app_configs()` — the same predicate the About page inventories
with, so "which apps are ours" is decided once and in one place. Explicit labels still work and
take precedence.

`./mutint test` reported `Ran 0 tests ... OK` before this existed, which is the worst available
way to fail: it says success.

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
`Population → TimePoint → Isolate → TechnicalReplicate → Sample`; mutations are
`Mutation` rows joined to samples by `MutationCall`.

Two things that are easy to miss:

- **Create the project through the view**, `POST /project/create/`, rather than
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

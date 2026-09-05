"""The test runner, so that a bare `test` finds the tests in an assembled project.

`./aledb test` works by ordinary unittest discovery: aledb-core's app packages sit directly
under the working directory. `./mutint test` did not, and said so in the least useful way
available -- "Ran 0 tests ... OK". Discovery walks the working directory, and an assembled
project's code lives in submodule directories named `aledb-core`, `aledb-compare` and so on.
Those names contain hyphens, so they can never be Python packages and discovery can never
descend into them, however they are laid out. The app packages inside them are importable
only because settings puts each submodule directory on `sys.path`.

So a bare run has to be told what to run, and the answer is already known: the apps this
deployment is made of. That is the same set the About page inventories, which is why the
predicate lives in `about_registry` rather than being restated here.

A silently empty test run is worse than a failing one -- it reports success. Explicit labels
still work exactly as before and take precedence.

The runner also points `ALEDB_STORE_DIR` at a temporary directory for the duration of the
run. Tests that exercise the importer write real files into the store, and one that forgets
`override_settings(ALEDB_STORE_DIR=...)` writes them into the *live* store instead. That is
not a harmless stray file: store paths are derived from primary keys, and a test database
numbers its experiments from 1, so such a test lands squarely on `experiments/1/` and
`experiments/2/` of whatever deployment it was run against and overwrites their references
with its own fixture.

It happened. `./mutint test` replaced a real REL606 reference with the 6 kb synthetic
example dataset, and the genome browser then drew empty tracks for all 29 samples in that
experiment -- while their BAMs, their coverage BigWigs and their `bam_stored` flags were all
perfectly intact, which is what made it look like a BAM problem for as long as it did.
Per-test overrides keep working and take precedence over this one; it is the backstop for
the tests that forget, including every test not yet written.
"""

import logging
import shutil
import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings

logger = logging.getLogger(__name__)


class AledbTestRunner(DiscoverRunner):
    def build_suite(self, test_labels=None, *args, **kwargs):
        # *args rather than a named `extra_tests`: Django passes it positionally in some
        # versions and has dropped it in others, and this override cares about neither.
        if not test_labels:
            from aledb_common.about_registry import first_party_app_configs

            test_labels = [cfg.name for cfg in first_party_app_configs()]
            logger.debug("no test labels given; running %d installed apps",
                         len(test_labels))
        return super().build_suite(test_labels, *args, **kwargs)

    # --- store isolation ----------------------------------------------------------------

    #: Tasks run inline under test. This is not the `task_always_eager` posture that is worth
    #: arguing against -- that one is about *development* hiding the difference between queued
    #: and immediate. A test asserting what an import produced should not also have to run a
    #: worker, and the one test that must exercise the real DatabaseBackend overrides this
    #: back (aledb_import/tests/test_tasks.py).
    TASKS = {'default': {'BACKEND': 'django.tasks.backends.immediate.ImmediateBackend'}}

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._tasks_override = override_settings(TASKS=self.TASKS)
        self._tasks_override.enable()
        # Held on the runner rather than module state: `teardown_test_environment` is the
        # only thing that needs it back, and it is the same instance.
        self._store_dir = tempfile.mkdtemp(prefix="aledb-test-store-")
        self._store_override = override_settings(ALEDB_STORE_DIR=self._store_dir)
        self._store_override.enable()
        logger.debug("tests write to a temporary store at %s", self._store_dir)

    def teardown_test_environment(self, **kwargs):
        # Unwound in the opposite order to setup, and defensively: a run that failed before
        # setup finished must still reach `super()` rather than raising over the real error.
        override = getattr(self, "_store_override", None)
        if override is not None:
            override.disable()
            self._store_override = None
        store_dir = getattr(self, "_store_dir", None)
        if store_dir is not None:
            shutil.rmtree(store_dir, ignore_errors=True)
            self._store_dir = None
        tasks = getattr(self, "_tasks_override", None)
        if tasks is not None:
            tasks.disable()
            self._tasks_override = None
        super().teardown_test_environment(**kwargs)

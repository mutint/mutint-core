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
"""

import logging

from django.test.runner import DiscoverRunner

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

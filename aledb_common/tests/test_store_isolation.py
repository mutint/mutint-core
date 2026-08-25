"""No test may write to the deployment's real store.

`AledbTestRunner` redirects `ALEDB_STORE_DIR` at a temporary directory for the whole run,
because a test that exercises the importer and forgets its own
`override_settings(ALEDB_STORE_DIR=...)` otherwise writes into the live store -- and store
paths are derived from primary keys, so a test database's experiment 2 overwrites the real
experiment 2's reference. That is not hypothetical; see the runner's docstring.

This asserts the redirection is actually in force. It fails the moment the hook is removed
from the runner, which is the only way the incident can recur.
"""

import os

from django.conf import settings
from django.test import TestCase

from aledb_common.base_settings import get_base_settings


class StoreIsolationTestCase(TestCase):

    def _configured_store(self):
        """Where this checkout's store would be were the runner not redirecting it.

        Read from `get_base_settings` rather than hardcoded, so the two cannot drift: the
        point of the test is that the live value and the running value differ.
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return get_base_settings(base_dir)["ALEDB_STORE_DIR"]

    def test_the_store_is_not_the_configured_one(self):
        self.assertNotEqual(os.path.abspath(self._configured_store()),
                            os.path.abspath(settings.ALEDB_STORE_DIR))

    def test_the_store_is_writable(self):
        """The redirect has to point somewhere usable, or every importer test fails with a
        confusing error instead of this one."""
        from aledb_common import store

        self.assertTrue(os.path.isdir(store.store_root()))
        probe = os.path.join(store.store_root(), "probe")
        store.ensure_dir(probe)
        self.assertTrue(os.path.isdir(probe))

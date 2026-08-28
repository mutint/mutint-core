"""Opening an experiment-scoped page before picking an experiment.

Every one of these pages resolves `?ale_experiment_id` through
`get_ale_experiment`, so arriving without one raises DoesNotExist. That is the
page's opening state, not a breakage. It used to fall through each view's
catch-all handler, which logged an ERROR with a traceback and showed the reader
Django's raw "AleExperiment matching query does not exist" -- so the log could
not be used to spot real breakages, and the message told nobody what to do.
"""

from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase

# page, the logger it should report through, and what it calls itself
# "/mutations/" is deliberately absent: the Compare page it served moved to the
# aledb-compare plugin, and core cannot see a plugin -- there is no plugin discovery in
# standalone aledb-core at all. Its equivalent lives in aledb_compare/tests/.
PAGES = [
    ("/mutations/breseq", "aledb_seq.views.breseq_table", "samples"),
    ("/stats/", "aledb_stats.views", "statistics"),
    ("/metadata/", "aledb_metadata.views", "metadata"),
]


class NoExperimentSelectedTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create(
            username="tester", first_name="Test", last_name="User", email="t@e.com",
            is_active=True, is_staff=True, is_superuser=True, date_joined=datetime.now())
        self.client.force_login(self.user)

    def test_every_page_says_to_pick_an_experiment(self):
        for page, _logger, what in PAGES:
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertContains(
                    response, "Select an experiment to see its %s." % what)
                self.assertNotContains(response, "matching query does not exist")

    def test_no_page_logs_it_as_an_error(self):
        for page, logger, _what in PAGES:
            with self.subTest(page=page):
                with self.assertLogs(logger, level="INFO") as captured:
                    self.client.get(page)
                self.assertEqual([], [r.levelname for r in captured.records
                                      if r.levelname not in ("INFO", "DEBUG")])
                self.assertEqual([], [r.getMessage() for r in captured.records
                                      if r.exc_info is not None])

    def test_it_names_the_page_it_came_from(self):
        for page, logger, what in PAGES:
            with self.subTest(page=page):
                with self.assertLogs(logger, level="INFO") as captured:
                    self.client.get(page)
                self.assertIn("%s with no experiment selected" % what,
                              [r.getMessage() for r in captured.records])

    def test_the_log_helpers_reach_the_record(self):
        """`extra=`, not a positional argument -- positionally they are dropped
        as %-format arguments and the structured fields never appear."""
        for page, logger, what in PAGES:
            with self.subTest(page=page):
                with self.assertLogs(logger, level="INFO") as captured:
                    self.client.get(page)
                record = next(r for r in captured.records
                              if r.getMessage().endswith("no experiment selected"))
                self.assertEqual(page, record.path)
                self.assertEqual("tester", str(record.userinfo["username"]))

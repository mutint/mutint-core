import os
import tempfile
import unittest

from aledb_common.util import get_git_hash, get_revision

__author__ = 'Patrick Phaneuf'


class TestUtil(unittest.TestCase):

    def test_get_hash(self):
        expected_hash_length = 40
        returned = len(get_git_hash())
        self.assertEqual(returned, expected_hash_length)

    def test_the_hash_is_a_string(self):
        """It used to return check_output's bytes, so `{{ git_hash }}` rendered
        b'56be47ad...' -- prefix and quotes included -- on every page that shows it. The
        length check above is true of the bytes too, which is why it never noticed."""
        self.assertIsInstance(get_git_hash(), str)

    def test_a_directory_that_is_not_a_repository_has_no_revision(self):
        """Rather than raising. This used to run at import time under a bare `try` that bound
        common_context only on success, so a checkout without .git left that name undefined
        and every request died in get_user_context() with a NameError."""
        self.assertIsNone(get_revision(tempfile.mkdtemp()))

    def test_a_repository_reports_a_short_and_a_full_revision(self):
        revision = get_revision(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        self.assertIsNotNone(revision)
        self.assertEqual(len(revision["full"]), 40)
        self.assertTrue(revision["full"].startswith(revision["short"]))

    def test_a_github_remote_becomes_a_commit_link(self):
        from aledb_common.util import _github_commit_url

        sha = "a" * 40
        expected = "https://github.com/barricklab/aledb-core/commit/" + sha
        self.assertEqual(
            _github_commit_url("https://github.com/barricklab/aledb-core.git", sha), expected)
        self.assertEqual(
            _github_commit_url("git@github.com:barricklab/aledb-core.git", sha), expected)

    def test_a_remote_that_is_not_github_gets_no_link(self):
        """This suite's submodules point at relative local paths, so they render plain."""
        from aledb_common.util import _github_commit_url

        self.assertIsNone(_github_commit_url("../aledb-core", "a" * 40))
        self.assertIsNone(_github_commit_url(None, "a" * 40))

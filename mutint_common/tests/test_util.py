import os
import tempfile
import unittest

from mutint_common.util import get_git_hash, get_revision

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
        from mutint_common.util import _github_commit_url

        sha = "a" * 40
        expected = "https://github.com/mutint/mutint-core/commit/" + sha
        self.assertEqual(
            _github_commit_url("https://github.com/mutint/mutint-core.git", sha), expected)
        self.assertEqual(
            _github_commit_url("git@github.com:mutint/mutint-core.git", sha), expected)

    def test_a_remote_that_is_not_github_gets_no_link(self):
        """This suite's submodules point at relative local paths, so they render plain."""
        from mutint_common.util import _github_commit_url

        self.assertIsNone(_github_commit_url("../mutint-core", "a" * 40))
        self.assertIsNone(_github_commit_url(None, "a" * 40))


class GeneListParsingTestCase(unittest.TestCase):
    """`Mutation.gene` holds two spellings of the same separator, so the reader takes both.

    A mutation spanning a gene range stores every name breseq listed, joined by
    `annotate.annotator` with a bare comma; every other shape is joined by the import path
    with ", ". Splitting on ", " alone read a 4,318-gene inversion as a single gene name
    23,003 characters long -- which is why the Gene column's expander never appeared on real
    data, and why a reader's ignored-gene list could not name any of those genes.

    The writer is deliberately left alone: `gene` is part of `Mutation.objects.get_or_create`'s
    key, so re-joining would fork every affected mutation on the next import.
    """

    def test_the_spelling_the_import_path_writes(self):
        from mutint_common.util import get_gene_list

        self.assertEqual(["thrA", "thrB"], get_gene_list("thrA, thrB"))

    def test_the_spelling_the_annotator_writes(self):
        """The defect: this used to come back as one 'gene' with commas inside it."""
        from mutint_common.util import get_gene_list

        self.assertEqual(["mokC", "nhaA", "nhaR"], get_gene_list("mokC,nhaA,nhaR"))

    def test_a_wide_range_is_every_name_and_not_one_long_one(self):
        from mutint_common.util import get_gene_list

        names = ["gene%04d" % index for index in range(4318)]
        self.assertEqual(names, get_gene_list(",".join(names)))

    def test_the_two_spellings_may_be_mixed(self):
        from mutint_common.util import get_gene_list

        self.assertEqual(["a", "b", "c"], get_gene_list("a, b,c"))

    def test_the_intragenic_brackets_are_still_stripped(self):
        from mutint_common.util import get_gene_list

        self.assertEqual(["insB-14", "ykfF"], get_gene_list("[insB-14],[ykfF]"))

    def test_a_single_gene_is_unchanged(self):
        """Including the literal 'None' the import path writes for an unannotated mutation."""
        from mutint_common.util import get_gene_list

        self.assertEqual(["thrA"], get_gene_list("thrA"))
        self.assertEqual(["None"], get_gene_list("None"))

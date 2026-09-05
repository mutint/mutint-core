"""Deriving a sample's coverage BigWig.

`bedGraphToBigWig` is not exercised here -- the suite must not shell out to it, and what that
would prove is its business anyway. What is worth pinning is everything around it: the
preconditions that produce a useful error instead of a confusing one, the chrom.sizes derived
from the reference's own index, the contract that a sample keeps its reads when its coverage
cannot be built, and -- since the counting moved in-process -- the weighting itself.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_common.tools import ToolMissing
from mutint_import import breseq_folder, coverage
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample


class ChromSizesTestCase(TestCase):
    def test_it_keeps_the_first_two_columns_of_the_fai(self):
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)

        fai = os.path.join(scratch, "reference.fasta.fai")
        with open(fai, "w") as handle:
            handle.write("test_ref\t20000\t10\t70\t71\n")
            handle.write("plasmid\t5000\t28000\t70\t71\n")

        out = coverage.chrom_sizes_from_fai(fai, os.path.join(scratch, "chrom.sizes"))
        with open(out) as handle:
            self.assertEqual(handle.read(), "test_ref\t20000\nplasmid\t5000\n")

    def test_an_index_naming_nothing_is_an_error_not_an_empty_file(self):
        """bedGraphToBigWig would otherwise fail with something far less clear."""
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)

        fai = os.path.join(scratch, "reference.fasta.fai")
        open(fai, "w").close()

        with self.assertRaises(coverage.CoverageError):
            coverage.chrom_sizes_from_fai(fai, os.path.join(scratch, "chrom.sizes"))


class BuildPreconditionsTestCase(TestCase):
    def setUp(self):
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        # find_user() prompts on stdin for a person matching no User, which is an EOFError
        # under the test runner -- so the user exists before the import reaches it.
        User.objects.create(username="tester", email="t@e.com", is_active=True)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="tester")
        self.reseq = Sample.objects.get()

    def test_a_sample_with_no_alignment_says_so(self):
        self.reseq.bam_stored = False
        self.reseq.save(update_fields=["bam_stored"])

        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.build_for(self.reseq)
        self.assertIn("no stored alignment", str(caught.exception))

    def test_a_flag_without_the_file_behind_it_says_so(self):
        os.remove(store.sample_path(self.reseq.id, store.SAMPLE_BAM))

        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.build_for(self.reseq)
        self.assertIn("missing", str(caught.exception))

    def test_a_missing_reference_index_says_what_it_was_for(self):
        os.remove(store.experiment_reference_path(
            self.reseq.experiment.id, store.REFERENCE_FAI))

        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.build_for(self.reseq)
        self.assertIn("reference index", str(caught.exception))

    def test_build_quietly_reports_failure_rather_than_raising(self):
        """The import path's contract: coverage is worth having, not worth rejecting a
        sample over."""
        self.reseq.bam_stored = False
        self.reseq.save(update_fields=["bam_stored"])

        self.assertFalse(coverage.build_quietly(self.reseq))
        self.reseq.refresh_from_db()
        self.assertFalse(self.reseq.coverage_stored)

    def test_a_missing_tool_is_swallowed_by_build_quietly(self):
        with override_settings(MUTINT_TOOLS_DIR=tempfile.mkdtemp()):
            def absent(_name):
                raise ToolMissing("bedGraphToBigWig is not installed.")

            original = coverage.require
            coverage.require = absent
            try:
                self.assertFalse(coverage.build_quietly(self.reseq))
            finally:
                coverage.require = original


class StoreTestCase(TestCase):
    def test_the_bigwig_is_an_allowed_sample_artifact(self):
        """sample_path is a whitelist and raises on anything not listed."""
        path = store.sample_path(1, store.SAMPLE_BIGWIG)
        self.assertTrue(path.endswith("coverage.bw"))

    def test_an_unknown_artifact_is_still_refused(self):
        with self.assertRaises(ValueError):
            store.sample_path(1, "../../etc/passwd")


class WeightedBedGraphTestCase(TestCase):
    """The weighting itself: each alignment counts 1/X1, not 1.

    breseq writes every read that maps to N places N times over, tagging each copy X1:i:N, so
    counting them as 1 apiece gives an IS element N times the depth it has. `coverage_output.cpp`
    accumulates `redundant_cov += 1.0/redundancy`, and this is the same sum.
    """

    SEQUENCES = [("test_ref", "A" * 1000)]

    def setUp(self):
        self.scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.sizes = os.path.join(self.scratch, "chrom.sizes")
        with open(self.sizes, "w") as handle:
            handle.write("test_ref\t1000\n")

    def _depths(self, reads):
        """`(depth_by_position, tally)` for a BAM built from `reads`."""
        bam = breseq_fixture.write_alignment_bam(
            os.path.join(self.scratch, "in.bam"), self.SEQUENCES, reads)
        out = os.path.join(self.scratch, "out.bedgraph")
        tally = coverage.write_weighted_bedgraph(bam, self.sizes, out)
        depth = {}
        with open(out) as handle:
            for line in handle:
                name, start, end, value = line.split()
                for position in range(int(start), int(end)):
                    depth[position] = float(value)
        return depth, tally

    def test_untagged_reads_each_count_one(self):
        """The compatibility guarantee. A BAM from anything but breseq has no X1, and must
        produce what `bedtools genomecov -ibam -bg` produced before this changed."""
        depth, tally = self._depths([("test_ref", 100, 50, None)] * 3)
        self.assertEqual(3.0, depth[100])
        self.assertNotIn(150, depth)          # end is exclusive
        self.assertFalse(tally.normalized)
        self.assertEqual(0, tally.tagged)

    def test_a_read_mapping_four_ways_counts_a_quarter(self):
        depth, tally = self._depths([("test_ref", 100, 50, 4)] * 4)
        self.assertEqual(1.0, depth[100])     # 4 reads x 1/4
        self.assertTrue(tally.normalized)
        self.assertEqual(4, tally.redundant)

    def test_the_is_element_case(self):
        """What this change is for, in the shape it actually occurs.

        Ten identical copies of an IS element; ten reads come off the family, and each maps
        equally well to all ten copies, so breseq writes each read ten times -- once per copy,
        every copy tagged X1:i:10. Every copy therefore carries ten alignment records.

        Unweighted that reads 10x at every copy, which is the bug. Weighted it reads 1x, and
        the ten copies together account for exactly the ten reads that exist.
        """
        tagged = [("test_ref", 100 * copy, 50, 10)
                  for copy in range(10) for _ in range(10)]
        depth, _ = self._depths(tagged)
        self.assertEqual([1.0] * 10, [depth[100 * copy] for copy in range(10)])

        # The same records with the tag stripped -- what the old pipeline saw.
        untagged = [(name, start, length, None) for name, start, length, _ in tagged]
        before, _ = self._depths(untagged)
        self.assertEqual([10.0] * 10, [before[100 * copy] for copy in range(10)])

    def test_unique_and_redundant_reads_add_up(self):
        depth, tally = self._depths([
            ("test_ref", 200, 50, None),   # untagged  -> 1
            ("test_ref", 200, 50, 1),      # X1 = 1    -> 1
            ("test_ref", 200, 50, 2),      # X1 = 2    -> 0.5
        ])
        self.assertEqual(2.5, depth[200])
        self.assertEqual(3, tally.alignments)
        self.assertEqual(2, tally.tagged)
        self.assertEqual(1, tally.redundant)

    def test_a_file_that_is_not_a_bam_is_this_module_s_own_error(self):
        """And so is swallowed by `build_quietly`, which is what keeps a bad BAM from
        failing the sample import that carries it.

        Regression: pysam raises ValueError for a header it cannot parse, which is not in
        `build_quietly`'s except clause -- so before it was translated, every import of a
        sample whose BAM pysam would not open reported a per-file error and lost the sample.
        The old pipeline never had this problem because bedtools failed as a subprocess.
        """
        not_a_bam = os.path.join(self.scratch, "nonsense.bam")
        with open(not_a_bam, "wb") as handle:
            handle.write(b"BAM\x01" + bytes(range(256)))
        with self.assertRaises(coverage.CoverageError) as caught:
            coverage.write_weighted_bedgraph(
                not_a_bam, self.sizes, os.path.join(self.scratch, "nope.bedgraph"))
        self.assertIn("nonsense.bam", str(caught.exception))

    def test_the_tag_is_not_stored_as_the_type_it_is_written_as(self):
        """The trap this feature would otherwise have fallen into, pinned as a fact.

        breseq spells the tag `X1:i:<n>` in SAM text, but htslib stores an integer aux tag in
        the narrowest type that fits -- so in the file a small redundancy is `C`, never `i`.
        Measured the same way on a real stored BAM: bowtie2's own `AS:i:` is `C` there too.

        A reader that matched on type `i` would find no tags at all, weight every read 1 and
        report success: the unnormalized answer, silently, which is the worst of the available
        failures. Asserted rather than trusted because nothing else in the suite would notice.
        """
        import pysam

        bam = breseq_fixture.write_alignment_bam(
            os.path.join(self.scratch, "typed.bam"), self.SEQUENCES,
            [("test_ref", 10, 20, 4)])
        with pysam.AlignmentFile(bam, "rb") as handle:
            tags = next(handle.fetch(until_eof=True)).get_tags(with_value_type=True)
        self.assertEqual("C", {name: kind for name, _, kind in tags}["X1"])

    def test_every_integer_width_the_tag_can_be_stored_in_is_read(self):
        """`c C s S i I` all mean the same number. htslib picks by magnitude and sign, so
        which one a given BAM uses is not something this code gets to assume."""
        for value_type in ("c", "C", "s", "S", "i", "I"):
            with self.subTest(value_type=value_type):
                bam = breseq_fixture.write_alignment_bam(
                    os.path.join(self.scratch, "w%s.bam" % value_type), self.SEQUENCES,
                    [("test_ref", 10, 20, 4)], tag_value_type=value_type)
                out = os.path.join(self.scratch, "w%s.bedgraph" % value_type)
                tally = coverage.write_weighted_bedgraph(bam, self.sizes, out)
                self.assertEqual(1, tally.redundant, value_type)
                self.assertIn("\t0.25\n", open(out).read())

    def test_depth_returns_to_nothing_after_the_read(self):
        """A difference array that forgot its closing -w would run to the end of the contig."""
        depth, _ = self._depths([("test_ref", 10, 20, None)])
        self.assertEqual(1.0, depth[29])
        self.assertNotIn(30, depth)
        self.assertNotIn(999, depth)

    def test_the_tally_describes_an_untagged_bam_in_words(self):
        """The only signal that a rebuild changed nothing, since no column records the rule."""
        _, tally = self._depths([("test_ref", 0, 10, None)])
        self.assertIn("NOT normalized", tally.describe())

    def test_a_bedgraph_row_is_written_for_every_covered_run(self):
        depth, _ = self._depths([("test_ref", 0, 10, None), ("test_ref", 5, 10, None)])
        self.assertEqual(1.0, depth[0])     # first read alone
        self.assertEqual(2.0, depth[5])     # both
        self.assertEqual(1.0, depth[14])    # second alone
        self.assertNotIn(15, depth)

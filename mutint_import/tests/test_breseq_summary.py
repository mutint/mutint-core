"""Reading sample statistics from breseq's data/summary.json.

These four numbers used to be scraped out of summary.html by table index -- 3rd table, 2nd
row, 5th column for mean coverage -- with a bare `except: None` that left them at zero when
the shape shifted. Nothing covered that. This does.
"""

import json
import os
import shutil
import tempfile

from django.test import TestCase

from mutint_import.breseq_summary import mean_coverage, read_breseq_summary

# Trimmed from a real breseq run (Ara+3 10000gen, Illumina); values are unmodified.
FIXTURE_SAMPLE = os.path.join(os.path.dirname(__file__), "0-0-1-1")


class ReadBreseqSummaryTestCase(TestCase):
    def test_reads_the_four_statistics(self):
        stats = read_breseq_summary(FIXTURE_SAMPLE)

        self.assertEqual(stats["reads"], 2353696)
        self.assertAlmostEqual(stats["average_read_length"], 139.5, places=1)
        self.assertAlmostEqual(stats["percentage_mapped"], 95.57, places=2)
        self.assertAlmostEqual(stats["mean_coverage"], 68.04, places=2)

    def test_a_sample_without_the_file_is_not_an_error(self):
        """A drop that omits summary.json still imports, just without statistics."""
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)

        self.assertIsNone(read_breseq_summary(empty))
        self.assertIsNone(read_breseq_summary("/nonexistent/path"))

    def test_unparseable_json_is_reported_not_raised(self):
        """The old scrape swallowed failures silently; this one logs and returns None."""
        broken = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, broken, True)
        os.makedirs(os.path.join(broken, "data"))
        with open(os.path.join(broken, "data", "summary.json"), "w") as handle:
            handle.write("{not json")

        with self.assertLogs("mutint_import.breseq_summary", level="ERROR"):
            self.assertIsNone(read_breseq_summary(broken))

    def test_zero_reads_does_not_divide_by_zero(self):
        empty_reads = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_reads, True)
        os.makedirs(os.path.join(empty_reads, "data"))
        with open(os.path.join(empty_reads, "data", "summary.json"), "w") as handle:
            json.dump({"reads": {"total_reads": 0, "total_bases": 0}}, handle)

        stats = read_breseq_summary(empty_reads)
        self.assertEqual(stats["average_read_length"], 0)
        self.assertEqual(stats["mean_coverage"], 0)


class MeanCoverageTestCase(TestCase):
    """One reference must reduce to exactly that reference's own average."""

    def test_single_reference_is_its_own_average(self):
        summary = {"references": {"reference": {
            "chr": {"coverage_average": 42.5, "length": 1000}}}}
        self.assertEqual(mean_coverage(summary), 42.5)

    def test_multiple_references_are_length_weighted(self):
        summary = {"references": {"reference": {
            "big": {"coverage_average": 100.0, "length": 900},
            "small": {"coverage_average": 10.0, "length": 100}}}}
        # (100*900 + 10*100) / 1000
        self.assertEqual(mean_coverage(summary), 91.0)

    def test_junction_only_references_are_skipped(self):
        """Junction-only entries are not real sequence and would skew the mean."""
        summary = {"references": {"reference": {
            "real": {"coverage_average": 50.0, "length": 1000},
            "junction": {"coverage_average": 900.0, "length": 50, "junction_only": True}}}}
        self.assertEqual(mean_coverage(summary), 50.0)

    def test_no_references_is_zero_not_an_error(self):
        self.assertEqual(mean_coverage({}), 0)
        self.assertEqual(mean_coverage({"references": {"reference": {}}}), 0)

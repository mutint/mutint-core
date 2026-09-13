"""The metadata.csv parser and the header rule, with no database."""

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

from mutint_import import metadata
from mutint_import.sample_names import parse_sample_identity

HEADER = "sample,population,time_point,data\n"


class ParseTestCase(SimpleTestCase):

    def test_a_row_places_its_inputs(self):
        parsed = metadata.parse(HEADER + "763A,Ara-2,500,Ara-2_500gen_763A.gd\n")
        row = parsed.rows[0]
        self.assertEqual(("763A", "Ara-2", 500), (row.sample, row.population, row.time_point))
        self.assertEqual(["Ara-2_500gen_763A.gd"], row.data)
        self.assertEqual(2, row.line)

    def test_columns_may_come_in_any_order_and_any_case(self):
        parsed = metadata.parse("Data,Time Point,POPULATION,Sample\nx.gd,5,p,s\n")
        row = parsed.rows[0]
        self.assertEqual(("s", "p", 5, ["x.gd"]),
                         (row.sample, row.population, row.time_point, row.data))

    def test_a_missing_column_is_refused_naming_it(self):
        with self.assertRaises(metadata.MetadataError) as raised:
            metadata.parse("sample,population,data\ns,p,x\n")
        self.assertIn("time_point", str(raised.exception))

    def test_comments_and_blank_lines_are_ignored_and_a_bom_is_tolerated(self):
        text = "﻿# a note\n\n" + HEADER + "# another\ns,p,1,x.gd\n\n"
        self.assertEqual(1, len(metadata.parse(text).rows))

    def test_semicolons_and_repeated_rows_gather_inputs(self):
        parsed = metadata.parse(HEADER + "s,p,1,a.gd;b.gd\ns,p,1,c.gd\n")
        self.assertEqual(1, len(parsed.rows))
        self.assertEqual(["a.gd", "b.gd", "c.gd"], parsed.rows[0].data)

    def test_population_without_a_time_point_is_refused_with_the_line(self):
        with self.assertRaises(metadata.MetadataError) as raised:
            metadata.parse(HEADER + "s,p,,x.gd\n")
        self.assertIn("line 2", str(raised.exception))
        with self.assertRaises(metadata.MetadataError):
            metadata.parse(HEADER + "s,,5,x.gd\n")

    def test_both_blank_is_an_unplaced_sample(self):
        row = metadata.parse(HEADER + "s,,,x.gd\n").rows[0]
        self.assertEqual(("", None), (row.population, row.time_point))

    def test_time_point_rules_are_the_sample_editors(self):
        self.assertEqual(500, metadata.parse(HEADER + "s,p,500.0,x\n").rows[0].time_point)
        for bad in ("500gen", "12.5", "-1"):
            with self.assertRaises(metadata.MetadataError, msg=bad):
                metadata.parse(HEADER + "s,p,%s,x\n" % bad)

    def test_a_row_naming_no_input_or_no_sample_is_refused(self):
        with self.assertRaises(metadata.MetadataError):
            metadata.parse(HEADER + "s,p,1,\n")
        with self.assertRaises(metadata.MetadataError):
            metadata.parse(HEADER + ",p,1,x.gd\n")

    def test_sample_type_is_optional_and_sets_is_clonal(self):
        self.assertIsNone(metadata.parse(HEADER + "s,p,1,x.gd\n").rows[0].is_clonal)
        typed = "sample,population,time_point,sample_type,data\n"
        for value, expected in (("population", False), ("Mixed", False), ("clone", True),
                                ("individual", True), ("isolate", True), ("", None)):
            row = metadata.parse(typed + "s,p,1,%s,x.gd\n" % value).rows[0]
            self.assertEqual(expected, row.is_clonal, value)
        with self.assertRaises(metadata.MetadataError) as raised:
            metadata.parse(typed + "s,p,1,plasmid,x.gd\n")
        self.assertIn("sample_type", str(raised.exception))
        # Repeated rows must agree about it.
        with self.assertRaises(metadata.MetadataError):
            metadata.parse(typed + "s,p,1,clone,a.gd\ns,p,1,population,b.gd\n")
        merged = metadata.parse(typed + "s,p,1,,a.gd\ns,p,1,clone,b.gd\n").rows[0]
        self.assertTrue(merged.is_clonal)

    def test_the_shipped_templates_parse(self):
        blank = finders.find("mutint_import/metadata-template.csv")
        example = finders.find("mutint_import/metadata-example.csv")
        self.assertIsNotNone(blank)
        self.assertIsNotNone(example)
        with open(blank, encoding="utf-8") as handle:
            self.assertEqual([], metadata.parse(handle.read()).rows)
        with open(example, encoding="utf-8") as handle:
            rows = metadata.parse(handle.read()).rows
        self.assertGreaterEqual(len(rows), 4)
        self.assertIn("s1", [token for row in rows for token in row.data])
        # The reads example the breseq launcher links: stems that match a pair, a two-lane
        # row, a population sample and an unplaced one.
        reads = finders.find("mutint_import/metadata-example-reads.csv")
        self.assertIsNotNone(reads)
        with open(reads, encoding="utf-8") as handle:
            parsed = metadata.parse(handle.read())
        self.assertGreaterEqual(len(parsed.rows), 4)
        mixed = parsed.lookup_stem(["Ara-2_2000gen_L001_R1.fastq.gz"])
        self.assertEqual("mix", mixed.sample)
        self.assertFalse(mixed.is_clonal)
        self.assertEqual("763A", parsed.lookup_stem(
            ["Ara-2_500gen_763A_R1.fastq.gz", "Ara-2_500gen_763A_R2.fastq.gz"]).sample)


class LookupTestCase(SimpleTestCase):

    def setUp(self):
        self.parsed = metadata.parse(
            HEADER + "a,p,1,alpha.gd\nb,p,2,beta\nc,p,3,gamma.vcf.gz\nd,p,4,folder\n")

    def test_exact_with_the_extension_optional_either_side(self):
        self.assertEqual("a", self.parsed.lookup("alpha").sample)
        self.assertEqual("a", self.parsed.lookup("alpha.gd").sample)
        self.assertEqual("b", self.parsed.lookup("beta.gd").sample)
        self.assertEqual("b", self.parsed.lookup("beta.vcf").sample)
        self.assertEqual("c", self.parsed.lookup("gamma").sample)
        self.assertEqual("d", self.parsed.lookup("folder").sample)
        self.assertIsNone(self.parsed.lookup("delta.gd"))
        self.assertIsNone(self.parsed.lookup("alph"))   # not a substring rule

    def test_two_rows_naming_one_input_is_a_conflict(self):
        parsed = metadata.parse(HEADER + "a,p,1,x.gd\nb,p,2,x\n")
        with self.assertRaises(metadata.MetadataConflict) as raised:
            parsed.lookup("x.gd")
        self.assertIn("rows 2 and 3", str(raised.exception))

    def test_stems_match_read_files_and_the_longest_wins(self):
        parsed = metadata.parse(HEADER + "one,p,1,S1\ntwelve,p,12,S12\n")
        self.assertEqual("one", parsed.lookup_stem(["S1_R1.fastq.gz", "S1_R2.fastq.gz"]).sample)
        self.assertEqual("twelve", parsed.lookup_stem(["S12_R1.fastq"]).sample)
        self.assertIsNone(parsed.lookup_stem(["S3_R1.fastq"]))

    def test_the_report_says_what_was_used(self):
        self.parsed.lookup("alpha.gd")
        report = self.parsed.report()
        self.assertEqual(["alpha.gd"], report["applied"])
        self.assertEqual(3, len(report["unmatched_rows"]))
        self.parsed.note("a note")
        self.assertEqual(["a note"], self.parsed.report()["warnings"])


class HeaderTestCase(SimpleTestCase):

    def test_headers_place_a_sample_under_any_synonym(self):
        row = metadata.coordinate_from_headers(
            {"SAMPLE": "763A", "TREATMENT": "Ara-2", "GENERATION": "500"})
        self.assertEqual(("763A", "Ara-2", 500), (row.sample, row.population, row.time_point))
        row = metadata.coordinate_from_headers({"name": "x", "condition": "c", "Time Point": "7"})
        self.assertEqual(("x", "c", 7), (row.sample, row.population, row.time_point))

    def test_a_header_may_say_the_sample_type(self):
        row = metadata.coordinate_from_headers({"SAMPLE": "x", "SAMPLE_TYPE": "population"})
        self.assertFalse(row.is_clonal)
        row = metadata.coordinate_from_headers({"sample": "x", "Sample Type": "isolate"})
        self.assertTrue(row.is_clonal)
        self.assertIsNone(metadata.coordinate_from_headers({"sample": "x"}).is_clonal)

    def test_an_exact_key_beats_a_synonym(self):
        row = metadata.coordinate_from_headers(
            {"TREATMENT": "LTEE", "POPULATION": "Ara-3", "TIME": "30000", "SAMPLE": "A"})
        self.assertEqual("Ara-3", row.population)

    def test_nothing_placing_is_none(self):
        self.assertIsNone(metadata.coordinate_from_headers({"REFSEQ": "x", "AUTHOR": "y"}))
        self.assertIsNone(metadata.coordinate_from_headers({}))

    def test_missing_fields_come_from_the_filename(self):
        identity = parse_sample_identity("3-30000-1-1")
        row = metadata.coordinate_from_headers(
            {"POPULATION": "Ara-3"}, filename_identity=identity, filename_stem="3-30000-1-1")
        self.assertEqual(("1-1", "Ara-3", 30000), (row.sample, row.population, row.time_point))
        row = metadata.coordinate_from_headers(
            {"SAMPLE": "clone7"}, filename_identity=None, filename_stem="whatever.gd")
        self.assertEqual(("clone7", "", None), (row.sample, row.population, row.time_point))
        row = metadata.coordinate_from_headers(
            {"POPULATION": "p", "TIME": "3"}, filename_identity=None, filename_stem="stem.gd")
        self.assertEqual("stem", row.sample)

    def test_a_header_placing_without_a_sample_or_a_name_is_refused(self):
        with self.assertRaises(metadata.MetadataError):
            metadata.coordinate_from_headers({"POPULATION": "p", "TIME": "3"})
        with self.assertRaises(metadata.MetadataError):
            metadata.coordinate_from_headers({"SAMPLE": "s", "POPULATION": "p"})


class FileTestCase(SimpleTestCase):

    def test_the_file_is_recognised_by_name_at_any_depth(self):
        self.assertTrue(metadata.is_metadata_file("Metadata.CSV"))
        self.assertTrue(metadata.is_metadata_file("drop/sub/metadata.csv"))
        self.assertFalse(metadata.is_metadata_file("other.csv"))
        mine, rest = metadata.split_paths(["a.gd", "x/metadata.csv", "b.vcf"])
        self.assertEqual((["x/metadata.csv"], ["a.gd", "b.vcf"]), (mine, rest))

    def test_the_slot_is_installed_for_the_block_only(self):
        parsed = metadata.parse(HEADER + "s,p,1,x.gd\n")
        self.assertIsNone(metadata.row_for("x.gd"))
        with metadata.applying(parsed):
            self.assertEqual("s", metadata.row_for("x").sample)
        self.assertIsNone(metadata.current())

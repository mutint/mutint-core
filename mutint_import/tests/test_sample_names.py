"""What `sample_names.parse_sample_identity` reads out of a filename.

No database: this is a pure function over a string, and the import path's own tests
(`test_gd_import`) cover what the answer then builds.
"""

import unittest

from mutint_import.sample_names import (
    SHAPE_AFIR, SHAPE_TRIPLE, SampleNameError, compose_sample_name, parse_sample_identity,
    sample_label,
)


def coordinate(sample_name):
    """The four identity fields, dropping the `shape` that says which rule read them."""
    identity = parse_sample_identity(sample_name)
    return None if identity is None else identity[:4]


class AfirTestCase(unittest.TestCase):
    """The four-integer form, unchanged except that two of the four come back as text."""

    def test_it_reads_all_four_fields(self):
        self.assertEqual(coordinate("3-30000-1-1"), ("3", 30000, "1", 1))

    def test_it_says_which_rule_read_the_name(self):
        """The caller labels the isolate for one shape and not the other."""
        self.assertEqual(parse_sample_identity("3-30000-1-1").shape, SHAPE_AFIR)

    def test_a_fifth_field_is_ignored(self):
        """Real names carry suffixes; the first four are the coordinate."""
        self.assertEqual(coordinate("3-30000-1-1-repeat"), ("3", 30000, "1", 1))

    def test_three_fields_is_not_a_coordinate(self):
        self.assertIsNone(parse_sample_identity("9-83-1"))

    def test_a_non_integer_field_refuses_the_whole_name(self):
        """Strict, so a name it cannot read is auto-numbered rather than half-read."""
        self.assertIsNone(parse_sample_identity("3-30000-1-A"))


class UnderscoreTripleTestCase(unittest.TestCase):

    def test_the_shape_this_exists_for(self):
        self.assertEqual(coordinate("Ara-2_500gen_763A"), ("Ara-2", 500, "763A", None))
        self.assertEqual(parse_sample_identity("Ara-2_500gen_763A").shape, SHAPE_TRIPLE)

    def test_the_time_point_keeps_only_its_leading_digits(self):
        for name, expected in (("Ara-2_500gen_763A", 500),
                               ("Ara-2_500_763A", 500),
                               ("Ara-2_50000gen_763A", 50000),
                               ("Ara-2_30000cd_763A", 30000)):
            with self.subTest(name=name):
                self.assertEqual(parse_sample_identity(name).time_point, expected)

    def test_the_ale_and_the_isolate_keep_every_character(self):
        """Stripping either would file `Ara-1` with `Ara+1`, or `763A` with `763B`."""
        identity = parse_sample_identity("Ara+1_500gen_763A")
        self.assertEqual(identity.population, "Ara+1")
        self.assertEqual(identity.name, "763A")

    def test_there_is_no_replicate(self):
        """None, not 1. This shape has no such field, and `sample_label` uses the
        difference to decide whether the label gets a suffix -- `763A` stays `763A` where
        an A-F-I-R name becomes `1-1`. It answered 1 while a replicate was a row that had
        to exist."""
        self.assertIsNone(parse_sample_identity("Ara-2_500gen_763A").replicate)

    def test_a_unit_may_sit_on_either_side_of_the_number(self):
        """`t0` used to be a label rather than a time point, and the whole name fell through.

        Time points are recorded in days, generations, transfers and hours, and `day7` is as
        clear as `7d` -- so a unit in front no longer costs the coordinate. The unit is
        discarded from both sides either way: `Sample.time_point` is one unit-less number.
        """
        for name, expected in (("Ara-2_t0_763A", 0), ("pop3_day7_clone2", 7),
                               ("Ara-2_500gen_763A", 500), ("Ara-2_h24_763A", 24)):
            with self.subTest(name=name):
                self.assertEqual(parse_sample_identity(name).time_point, expected)

    def test_a_middle_field_with_no_digits_is_still_not_a_time_point(self):
        """What was widened is where the digits may sit, not whether there have to be any."""
        self.assertIsNone(parse_sample_identity("Ara-2_gen_763A"))

    def test_two_fields_are_refused(self):
        """No telling whether the ALE or the isolate is the one missing."""
        self.assertIsNone(parse_sample_identity("Ara-2_500gen"))

    def test_four_fields_are_refused(self):
        self.assertIsNone(parse_sample_identity("Ara-2_500gen_763A_rerun"))

    def test_an_empty_field_is_refused(self):
        for name in ("_500gen_763A", "Ara-2_500gen_", "Ara-2__763A"):
            with self.subTest(name=name):
                self.assertIsNone(parse_sample_identity(name))

    def test_surrounding_space_is_trimmed(self):
        """A folder named with a stray space must not become a different ALE."""
        self.assertEqual(coordinate("Ara-2 _500gen_ 763A"), ("Ara-2", 500, "763A", None))


class NeitherShapeTestCase(unittest.TestCase):

    def test_a_plain_name_is_no_coordinate(self):
        self.assertIsNone(parse_sample_identity("everything"))

    def test_and_neither_is_an_empty_one(self):
        self.assertIsNone(parse_sample_identity(""))

    def test_afir_wins_when_a_name_satisfies_both(self):
        """Contrived, but the precedence has to be stated somewhere rather than inferred
        from the order of two `or`ed calls."""
        self.assertEqual(coordinate("1-2-3-4-x_500gen_y"), ("1", 2, "3", 4))

    def test_and_a_name_only_the_triple_reads_falls_to_it(self):
        """The same name with a non-integer in the fourth dash field."""
        self.assertEqual(coordinate("1-2-3-x_500gen_y"), ("1-2-3-x", 500, "y", None))


class SampleLabelTestCase(unittest.TestCase):
    """What the two parsed halves become in the database.

    The replicate stopped being a row of its own -- `1-1500-1-1` and `1-1500-1-2` are two
    samples in one flask rather than one isolate with two replicates beneath it -- so the
    name's last field is a suffix on the label.
    """

    def test_an_afir_name_keeps_its_replicate(self):
        self.assertEqual(sample_label("1", 2), "1-2")

    def test_including_when_the_replicate_is_one(self):
        """Not "appended only when it is not 1". A conditional would make `1` and `1-2`
        siblings, which reads as two unrelated samples rather than two replicates of one --
        and it would make the rule depend on a value rather than on the shape of the name.
        """
        self.assertEqual(sample_label("1", 1), "1-1")

    def test_a_shape_with_no_replicate_gets_no_suffix(self):
        """`Ara-2_500gen_763A` says `763A` and nothing more; inventing a `-1` would put a
        number in a label the person did not write."""
        self.assertEqual(sample_label("763A", None), "763A")

    def test_the_whole_pipeline_from_a_filename(self):
        for name, expected in (("3-30000-1-1", "1-1"),
                               ("3-30000-1-2", "1-2"),
                               ("Ara-2_500gen_763A", "763A")):
            with self.subTest(name=name):
                identity = parse_sample_identity(name)
                self.assertEqual(sample_label(identity.name, identity.replicate),
                                 expected)


class ComposeTestCase(unittest.TestCase):
    """`compose_sample_name`, the inverse of the parser above.

    A form hands it three parts rather than a joined string, so the convention for turning
    them into a name lives here and can change without the form changing with it.
    """

    def test_the_three_parts_become_a_name_the_parser_reads_back(self):
        for parts, expected in ((("Ara-2", "500", "763A"), "Ara-2_500_763A"),
                                (("3", "30000", "1-1"), "3_30000_1-1"),
                                (("Ara+1", "0", "763A"), "Ara+1_0_763A")):
            with self.subTest(parts=parts):
                name = compose_sample_name(*parts)
                self.assertEqual(name, expected)
                identity = parse_sample_identity(name)
                self.assertEqual(
                    (identity.population, str(identity.time_point),
                     sample_label(identity.name, identity.replicate)),
                    parts)

    def test_a_sample_alone_is_left_unplaced(self):
        """No population and no time point is the auto-numbered case: the name is the sample,
        and it carries no coordinate for the importer to read."""
        for sample in ("s1", "my_clone", "Ara-2-500"):
            with self.subTest(sample=sample):
                self.assertEqual(compose_sample_name("", "", sample), sample)
                self.assertIsNone(parse_sample_identity(sample))

    def test_a_population_without_a_time_point_is_refused(self):
        """There is no spelling for one: the triple shape needs digits in the middle."""
        for parts, field in ((("Ara-2", "", "763A"), "time_point"),
                             (("", "500", "763A"), "population")):
            with self.subTest(parts=parts):
                with self.assertRaises(SampleNameError) as caught:
                    compose_sample_name(*parts)
                self.assertEqual(caught.exception.field, field)

    def test_a_sample_name_is_required(self):
        with self.assertRaises(SampleNameError) as caught:
            compose_sample_name("Ara-2", "500", "  ")
        self.assertEqual(caught.exception.field, "sample")

    def test_a_space_inside_a_part_is_allowed_and_survives_the_round_trip(self):
        """It used to be refused, and the rule was never the parser's or the column's.

        `parse_sample_identity` has no character rule, `Population.name` is a plain CharField,
        and the sample editor checks only strip/non-empty/length -- so a coordinate somebody
        could type on the edit page was one no name could spell. The separator is `_`, so a
        space inside a part is unambiguous.
        """
        for parts in (("Ara 2", "500", "763A"), ("Ara-2", "500", "763 A")):
            with self.subTest(parts=parts):
                name = compose_sample_name(*parts)
                identity = parse_sample_identity(name)
                self.assertIsNotNone(identity, name)
                self.assertEqual(
                    (identity.population, str(identity.time_point),
                     sample_label(identity.name, identity.replicate)),
                    parts)

    def test_a_leading_space_is_still_refused(self):
        """Stripped rather than refused, in fact -- and what is left has to stand on its own."""
        self.assertEqual(compose_sample_name(" Ara-2 ", "500", "763A"), "Ara-2_500_763A")
        with self.assertRaises(SampleNameError) as caught:
            compose_sample_name("Ara-2", "500", " ")
        self.assertEqual(caught.exception.field, "sample")

    def test_a_fractional_time_point_is_refused(self):
        """`Sample.time_point` is a float and holds 12.5; the *name* cannot, because the
        parser reads leading digits only and would silently file it under 12."""
        with self.assertRaises(SampleNameError) as caught:
            compose_sample_name("Ara-2", "12.5", "763A")
        self.assertEqual(caught.exception.field, "time_point")

    def test_an_underscore_in_a_part_is_caught_by_reading_the_name_back(self):
        """No rule about characters catches this -- `Ara_2` is a fine population name, and
        `Ara_2_500_763A` is four fields, so the coordinate would be lost."""
        with self.assertRaises(SampleNameError) as caught:
            compose_sample_name("Ara_2", "500", "763A")
        self.assertIn("would not be read back", str(caught.exception))

    def test_an_unplaced_name_that_would_parse_is_refused(self):
        """The other half of the round trip: `x_5_y` alone would be read as a whole
        coordinate, placing the sample somewhere nobody asked for."""
        with self.assertRaises(SampleNameError) as caught:
            compose_sample_name("", "", "x_5_y")
        self.assertEqual(caught.exception.field, "sample")
        self.assertIn("would be read as population x", str(caught.exception))

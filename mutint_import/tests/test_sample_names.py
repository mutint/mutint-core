"""What `sample_names.parse_sample_identity` reads out of a filename.

No database: this is a pure function over a string, and the import path's own tests
(`test_gd_import`) cover what the answer then builds.
"""

import unittest

from mutint_import.sample_names import (
    SHAPE_AFIR, SHAPE_TRIPLE, parse_sample_identity, sample_label,
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
                self.assertEqual(parse_sample_identity(name).flask, expected)

    def test_the_ale_and_the_isolate_keep_every_character(self):
        """Stripping either would file `Ara-1` with `Ara+1`, or `763A` with `763B`."""
        identity = parse_sample_identity("Ara+1_500gen_763A")
        self.assertEqual(identity.ale, "Ara+1")
        self.assertEqual(identity.isolate, "763A")

    def test_there_is_no_replicate(self):
        """None, not 1. This shape has no such field, and `sample_label` uses the
        difference to decide whether the label gets a suffix -- `763A` stays `763A` where
        an A-F-I-R name becomes `1-1`. It answered 1 while a replicate was a row that had
        to exist."""
        self.assertIsNone(parse_sample_identity("Ara-2_500gen_763A").replicate)

    def test_a_time_point_with_no_leading_digit_is_not_one(self):
        """`t0` is a label, and a flask number is genuinely a number."""
        self.assertIsNone(parse_sample_identity("Ara-2_t0_763A"))

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
                self.assertEqual(sample_label(identity.isolate, identity.replicate),
                                 expected)

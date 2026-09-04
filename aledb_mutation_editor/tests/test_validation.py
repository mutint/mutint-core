"""Whether a hand-entered mutation is well-formed, and whether it would do anything.

Pure functions over a record dict and a fake reference, so none of this touches the database.
The fake is a handful of bases rather than a genome: every rule here is about a span of
sequence, and a real reference would only make the arithmetic harder to read.

The ordering assertions matter as much as the rules. `get_sequence_1` slices a plain string,
so an out-of-range span returns the wrong bases without raising -- a no-op check that ran
before the bounds check would answer confidently and wrongly. `NeverLoads` is what pins that.
"""

from django.test import SimpleTestCase

from aledb_mutation_editor import validation


class FakeReference:
    """The two calls `validation` makes of a real `ReferenceSequences`."""

    def __init__(self, sequences, repeats=()):
        self.sequences = {seq_id: bases.upper() for seq_id, bases in sequences.items()}
        self.repeats = set(repeats)

    def __contains__(self, seq_id):
        return seq_id in self.sequences

    def get_sequence_1(self, seq_id, start_1, end_1):
        # Deliberately as unguarded as the real one, so a test that reaches it out of range
        # sees the same wrong answer production would.
        return self.sequences[seq_id][start_1 - 1:end_1]

    def repeat_family_sequence(self, repeat_name, strand):
        return "ATGCATGC" if repeat_name in self.repeats else ""


class FakeReferenceRow:
    """Stands in for `ReferenceSequence`; only `seq_ids` is read."""

    def __init__(self, seq_ids):
        self.seq_ids = seq_ids


class NeverLoads:
    """A loader that fails the test if it is called."""

    def __init__(self, testcase):
        self.testcase = testcase

    def __call__(self):
        self.testcase.fail(
            "the reference was loaded for a check that cannot need it; loading parses the "
            "whole genome, and an out-of-range span must be refused before anything reads it")


class ValidationTestCase(SimpleTestCase):
    """Shared fixture: one contig, one annotated repeat family."""

    SEQ = "NC_000913"
    #        1234567890123456789012
    BASES = "ACGTTTAAACCCGGGATCGATG"

    def setUp(self):
        self.row = FakeReferenceRow([{"id": self.SEQ, "length": len(self.BASES)}])
        self.references = FakeReference({self.SEQ: self.BASES}, repeats={"IS150"})

    def load(self):
        return self.references

    def check(self, mutation_type, reference_row="default", loader="default", **fields):
        fields.setdefault("seq_id", self.SEQ)
        return validation.validate_record(
            fields, mutation_type,
            reference_row=self.row if reference_row == "default" else reference_row,
            load_references=self.load if loader == "default" else loader)

    def assertOk(self, mutation_type, **fields):
        attributes, errors = self.check(mutation_type, **fields)
        self.assertEqual({}, errors)
        return attributes

    def assertRefused(self, mutation_type, field, **fields):
        _, errors = self.check(mutation_type, **fields)
        self.assertIn(field, errors, "expected a complaint about %r, got %r" % (field, errors))
        return errors[field]


class ShapeTestCase(ValidationTestCase):

    def test_an_unknown_type_is_refused_before_anything_else(self):
        _, errors = self.check("XYZ", position=1, new_seq="A")
        self.assertEqual({"mutation_type": "Choose a mutation type."}, errors)

    def test_every_required_field_is_named_when_missing(self):
        """The message names the type, because which fields are required depends on it."""
        _, errors = self.check("MOB", position=5)
        for field in ("repeat_name", "strand", "duplication_size"):
            self.assertIn(field, errors)
            self.assertIn("MOB", errors[field])

    def test_a_blank_string_counts_as_missing(self):
        self.assertRefused("DEL", "size", position=5, size="   ")

    def test_a_non_numeric_integer_is_refused(self):
        self.assertIn("whole number", self.assertRefused("DEL", "size", position=5, size="wide"))

    def test_position_is_one_based(self):
        self.assertIn("1-based", self.assertRefused("DEL", "position", position=0, size=2))

    def test_a_size_of_zero_covers_nothing(self):
        self.assertIn("no bases", self.assertRefused("DEL", "size", position=5, size=0))

    def test_strand_is_one_or_minus_one(self):
        self.assertIn("1 or -1", self.assertRefused(
            "MOB", "strand", position=5, repeat_name="IS150", strand=2, duplication_size=9))

    def test_bases_outside_ACGTN_are_refused(self):
        self.assertIn("A, C, G, T", self.assertRefused("INS", "new_seq", position=5, new_seq="AXG"))

    def test_bases_are_upper_cased(self):
        self.assertEqual("GGG", self.assertOk("INS", position=5, new_seq="ggg")["new_seq"])

    def test_a_snp_takes_exactly_one_base(self):
        """Carried over verbatim from another type rather than truncated, so the message can
        say what to do with it."""
        self.assertIn("use SUB", self.assertRefused("SNP", "new_seq", position=5, new_seq="AT"))

    def test_a_region_must_be_seq_id_start_end(self):
        self.assertIn("seq_id:start-end", self.assertRefused(
            "CON", "region", position=1, size=3, region="1000..2000"))

    def test_a_region_may_be_written_backwards(self):
        """breseq reads a reversed region as the reverse complement."""
        self.assertOk("CON", position=1, size=3, region="%s:9-7" % self.SEQ)


class SequenceIndependentTestCase(ValidationTestCase):

    def test_an_amp_to_one_copy_is_refused(self):
        self.assertIn("change nothing", self.assertRefused(
            "AMP", "new_copy_number", position=1, size=3, new_copy_number=1))

    def test_it_is_refused_without_a_reference_too(self):
        """Nothing about this needs the bases, so drawing the line at 'a reference happens to
        be stored' would be arbitrary."""
        _, errors = self.check("AMP", reference_row=None, loader=None,
                               position=1, size=3, new_copy_number=1)
        self.assertIn("new_copy_number", errors)

    def test_a_copy_number_below_one_is_a_deletion(self):
        self.assertIn("deletion", self.assertRefused(
            "AMP", "new_copy_number", position=1, size=3, new_copy_number=0))

    def test_two_copies_is_fine(self):
        self.assertOk("AMP", position=1, size=3, new_copy_number=2)


class BoundsTestCase(ValidationTestCase):

    def test_an_unknown_contig_is_refused(self):
        self.assertIn("not a sequence", self.assertRefused(
            "DEL", "position", seq_id="NC_999999", position=1, size=2))

    def test_a_position_past_the_end_is_refused(self):
        message = self.assertRefused("SNP", "position", position=99, new_seq="A")
        self.assertIn("runs past the end", message)
        self.assertIn("22 bases", message)

    def test_a_span_running_off_the_end_is_refused(self):
        self.assertRefused("DEL", "position", position=20, size=10)

    def test_the_last_base_is_in_range(self):
        self.assertOk("DEL", position=len(self.BASES), size=1)

    def test_bounds_are_checked_before_the_bases_are_read(self):
        """The assertion this whole ordering exists for."""
        _, errors = self.check("SNP", loader=NeverLoads(self), position=99, new_seq="A")
        self.assertIn("position", errors)

    def test_a_deletion_never_loads_the_reference(self):
        """Nothing about a DEL's validity depends on which bases it removes, and loading
        parses the whole genome."""
        _, errors = self.check("DEL", loader=NeverLoads(self), position=5, size=3)
        self.assertEqual({}, errors)

    def test_a_regions_own_bounds_are_checked(self):
        self.assertIn("source region", self.assertRefused(
            "CON", "region", position=1, size=3, region="%s:1-500" % self.SEQ))

    def test_a_regions_contig_is_checked(self):
        self.assertRefused("CON", "region", position=1, size=3, region="NC_999999:1-3")


class NoOpTestCase(ValidationTestCase):
    """Bases: ACGTTTAAACCCGGGATCGATG, 1-based."""

    def test_a_snp_to_the_base_already_there_is_refused(self):
        message = self.assertRefused("SNP", "new_seq", position=1, new_seq="A")
        self.assertIn("already A", message, "the message should name the base in the way")

    def test_a_snp_to_a_different_base_is_fine(self):
        self.assertOk("SNP", position=1, new_seq="T")

    def test_a_sub_matching_the_reference_is_refused(self):
        self.assertIn("already the sequence", self.assertRefused(
            "SUB", "new_seq", position=1, size=4, new_seq="ACGT"))

    def test_a_sub_of_the_same_length_that_differs_is_fine(self):
        self.assertOk("SUB", position=1, size=4, new_seq="TTTT")

    def test_an_insertion_is_never_a_no_op(self):
        """It adds bases whatever they are, even ones matching its neighbours."""
        self.assertOk("INS", position=1, new_seq="A")

    def test_a_deletion_is_never_a_no_op(self):
        self.assertOk("DEL", position=1, size=1)

    def test_a_palindromic_inversion_is_refused(self):
        """A span that is its own reverse complement inverts to itself.

        Positions 1-4 are ACGT, whose reverse complement is ACGT -- the classic case, and the
        reason this rule is worth having: it looks like a perfectly ordinary inversion.
        """
        self.assertIn("own reverse complement",
                      self.assertRefused("INV", "size", position=1, size=4))

    def test_a_non_palindromic_inversion_is_fine(self):
        """ACG at 1-3 reverse-complements to CGT."""
        self.assertOk("INV", position=1, size=3)

    def test_a_single_base_inversion_is_not_a_no_op(self):
        """revcomp("A") is "T", so a one-base inversion does change the base."""
        self.assertOk("INV", position=1, size=1)

    def test_a_conversion_from_matching_sequence_is_refused(self):
        self.assertIn("already matches", self.assertRefused(
            "CON", "region", position=1, size=4, region="%s:1-4" % self.SEQ))

    def test_a_conversion_from_different_sequence_is_fine(self):
        self.assertOk("CON", position=1, size=3, region="%s:5-7" % self.SEQ)

    def test_a_reversed_region_is_compared_reverse_complemented(self):
        """ACG at 1-3; its reverse complement CGT sits nowhere, so reading 3-1 backwards
        gives CGT and the conversion is a real change."""
        self.assertOk("CON", position=1, size=3, region="%s:3-1" % self.SEQ)

    def test_an_int_is_validated_exactly_as_a_con(self):
        """Their field sets are identical and so is their meaning here."""
        self.assertIn("already matches", self.assertRefused(
            "INT", "region", position=1, size=4, region="%s:1-4" % self.SEQ))


class MobTestCase(ValidationTestCase):

    def test_a_known_repeat_family_is_fine(self):
        self.assertOk("MOB", position=5, repeat_name="IS150", strand=1, duplication_size=9)

    def test_an_unknown_repeat_family_is_refused(self):
        message = self.assertRefused(
            "MOB", "repeat_name", position=5, repeat_name="IS999", strand=1,
            duplication_size=9)
        self.assertIn("IS999", message, "the message should name the element it looked for")

    def test_a_duplication_size_of_zero_is_allowed(self):
        """A MOB that duplicates nothing at the target site is ordinary."""
        self.assertOk("MOB", position=5, repeat_name="IS150", strand=1, duplication_size=0)

    def test_a_negative_duplication_size_is_allowed(self):
        """It records a deletion at the insertion site, and real .gd files carry them."""
        self.assertOk("MOB", position=5, repeat_name="IS150", strand=-1, duplication_size=-3)


class NoReferenceTestCase(ValidationTestCase):
    """An experiment whose .gd arrived before a reference was established."""

    def check(self, mutation_type, **fields):
        fields.setdefault("seq_id", self.SEQ)
        return validation.validate_record(fields, mutation_type, reference_row=None,
                                          load_references=NeverLoads(self))

    def test_shape_is_still_enforced(self):
        _, errors = self.check("MOB", position=5, repeat_name="IS150", strand=7,
                               duplication_size=1)
        self.assertIn("strand", errors)

    def test_a_no_op_snp_is_accepted_because_nothing_can_know(self):
        """Silently accepting is the honest answer; the page says the check is off."""
        _, errors = self.check("SNP", position=1, new_seq="A")
        self.assertEqual({}, errors)

    def test_an_out_of_range_position_is_accepted(self):
        _, errors = self.check("DEL", position=10 ** 9, size=1)
        self.assertEqual({}, errors)


class SchemaTestCase(SimpleTestCase):

    def test_the_schema_covers_every_mutation_type(self):
        schema = validation.form_schema()
        self.assertEqual(list(validation.MUTATION_TYPES),
                         [entry["name"] for entry in schema["types"]])

    def test_the_field_sets_come_from_the_genomediff_package(self):
        """Restating them here would give the form a second opinion about what a MOB needs."""
        from genomediff.records import TYPE_SPECIFIC_FIELDS

        for entry in validation.form_schema()["types"]:
            self.assertEqual(list(TYPE_SPECIFIC_FIELDS[entry["name"]]), entry["fields"])

    def test_every_type_starts_with_seq_id_and_position(self):
        for entry in validation.form_schema()["types"]:
            self.assertEqual(["seq_id", "position"], entry["fields"][:2])

    def test_every_field_the_form_can_show_has_help_text(self):
        schema = validation.form_schema()
        for entry in schema["types"]:
            for field in entry["fields"]:
                self.assertIn(field, schema["help"])


class PackageGuardTestCase(ValidationTestCase):
    """Where our validator defers to `genomediff.schema`, and where it deliberately does not.

    The point of delegating is that breseq's rules live in one place. The point of these tests
    is the second half: two of our rules are *stricter* than breseq's, and a future tidy-up
    that "simplifies" them away would widen what the form accepts without anything failing.
    """

    def test_the_package_decides_and_we_word_it(self):
        """A guarded field shows our sentence, not breseq's phrasing."""
        message = self.assertRefused("SNP", "position", position=0, new_seq="T")

        self.assertIn("1-based", message)
        self.assertNotIn("Expected positive integral value", message,
                         "breseq's wording is right in a .gd and wrong beside a form input")

    def test_every_sentence_of_ours_replaces_a_rule_the_package_actually_has(self):
        """A key here for a field breseq does not guard would never be reached, and would sit
        looking like a rule the form enforces when nothing does."""
        from genomediff.schema import field_type

        for field in validation.GUARD_MESSAGES:
            with self.subTest(field=field):
                self.assertIsNotNone(
                    field_type("SNP", field) or field_type("MOB", field)
                    or field_type("AMP", field),
                    "%s is not guarded by the package, so our sentence is unreachable" % field)

    def test_our_rules_and_the_packages_agree_on_a_valid_record(self):
        """Every type the form offers, accepted by both."""
        from genomediff.schema import check_field

        # Bases are ACGTTTAAACCCGGGATCGATG, so position 5 is a T and 5-7 is TTA. The values
        # below are chosen to be real changes -- an accidental no-op here would fail for a
        # reason that has nothing to do with what this test is about.
        cases = {
            "SNP": {"position": 5, "new_seq": "A"},
            "SUB": {"position": 5, "size": 3, "new_seq": "GGG"},
            "DEL": {"position": 5, "size": 3},
            "INS": {"position": 5, "new_seq": "GGA"},
            "INV": {"position": 1, "size": 3},
            "AMP": {"position": 1, "size": 3, "new_copy_number": 2},
            "MOB": {"position": 5, "repeat_name": "IS150", "strand": 1,
                    "duplication_size": 9},
            "CON": {"position": 1, "size": 3, "region": "%s:5-7" % self.SEQ},
            "INT": {"position": 1, "size": 3, "region": "%s:5-7" % self.SEQ},
        }
        for mutation_type, fields in cases.items():
            with self.subTest(mutation_type=mutation_type):
                attributes = self.assertOk(mutation_type, **fields)
                for key, value in attributes.items():
                    self.assertIsNone(
                        check_field(mutation_type, key, value),
                        "%s.%s passed our validator but not breseq's guard" % (
                            mutation_type, key))

    # --- the two places we are stricter ---------------------------------------------------

    def test_a_size_of_zero_passes_the_package_and_we_still_refuse_it(self):
        """breseq types `size` as a NonNegativeInteger, so zero is legal to it. A DEL
        covering no bases deletes nothing, and this form is where somebody types it."""
        from genomediff.schema import check_field

        self.assertIsNone(check_field("DEL", "size", 0), "the package accepts it")
        self.assertIn("no bases", self.assertRefused("DEL", "size", position=5, size=0))

    def test_an_empty_new_seq_passes_the_package_and_we_still_refuse_it(self):
        """`is_base_sequence('')` is vacuously true -- every character of nothing is a base."""
        from genomediff.schema import check_field

        self.assertIsNone(check_field("INS", "new_seq", ""), "the package accepts it")
        self.assertRefused("INS", "new_seq", position=5, new_seq="")

    def test_a_multi_base_snp_passes_the_package_and_we_still_refuse_it(self):
        from genomediff.schema import check_field

        self.assertIsNone(check_field("SNP", "new_seq", "AT"))
        self.assertIn("use SUB", self.assertRefused("SNP", "new_seq", position=5, new_seq="AT"))

    def test_the_semantic_rules_have_no_counterpart_in_the_package(self):
        """The reason the whole semantic validator stays: `check_field` is well-formedness
        only, and breseq's own reference-aware check is equally generic."""
        from genomediff.schema import check_field

        self.assertIsNone(check_field("SNP", "new_seq", "A"), "a no-op SNP is well-formed")
        self.assertIsNone(check_field("AMP", "new_copy_number", 1), "so is an AMP to 1 copy")
        self.assertIsNone(check_field("INV", "size", 4), "so is a palindromic inversion")

        # And we refuse all three.
        self.assertRefused("SNP", "new_seq", position=1, new_seq="A")
        self.assertRefused("AMP", "new_copy_number", position=1, size=3, new_copy_number=1)
        self.assertRefused("INV", "size", position=1, size=4)


class RecordApiTestCase(SimpleTestCase):
    """Two semantics of the package's API that are easy to assume wrongly."""

    def test_get_without_a_default_raises_rather_than_returning_None(self):
        """It is not `dict.get`. Every call site has to pass a default."""
        from genomediff.records import Record

        record = Record("SNP", 1, parent_ids=None, seq_id="ref", position=5, new_seq="T")

        self.assertEqual(5, record.get("position"))
        self.assertIsNone(record.get("frequency", None))
        with self.assertRaises(KeyError):
            record.get("frequency")

    def test_set_refuses_a_value_breseq_would_reject_and_keeps_the_old_one(self):
        from genomediff.records import Record

        record = Record("SNP", 1, parent_ids=None, seq_id="ref", position=5, new_seq="T")

        with self.assertRaises(ValueError):
            record.set("position", 0)
        self.assertEqual(5, record.get("position"), "the refused write left it alone")

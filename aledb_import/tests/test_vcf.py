"""Normalising a VCF variant, and what it is then called.

The conversion rules alone -- no database, no reference, no import. Two groups matter:

- **the differential**, where we intend to agree with `gdtools VCF2GD` and the fixtures are
  its actual output, captured by running breseq 0.50 rather than transcribed from its docs;
- **the divergences**, each with a test named for its reason, because a difference from the
  reference implementation that nobody wrote down is indistinguishable from a bug.
"""

import io
import random

from django.test import SimpleTestCase

from aledb_import import vcf


def convert(position, ref, alt, seq_id="testref"):
    """One REF/ALT pair as its GenomeDiff record."""
    trimmed_position, trimmed_ref, trimmed_alt = vcf.trim(position, ref, alt)
    return vcf.classify(
        vcf.Variant(seq_id, trimmed_position, trimmed_ref, trimmed_alt, 1, None))


def fields(record):
    """The record without seq_id, which every case here shares."""
    return {key: value for key, value in record.items() if key != "seq_id"}


class GdtoolsAgreementTestCase(SimpleTestCase):
    """Cases where `gdtools VCF2GD` is right and we match it exactly.

    Fixtures are its real output. Run against breseq 0.50 as:

        gdtools VCF2GD -r canonical.gff3 -o /dev/stdout in.vcf
    """

    def test_a_snp(self):
        self.assertEqual({"type": "SNP", "position": 5000, "new_seq": "G"},
                         fields(convert(5000, "T", "G")))

    def test_a_canonical_deletion(self):
        # gdtools: DEL testref 101 3
        self.assertEqual({"type": "DEL", "position": 101, "size": 3},
                         fields(convert(100, "ACGT", "A")))

    def test_a_canonical_insertion(self):
        # gdtools: INS testref 100 CGT
        self.assertEqual({"type": "INS", "position": 100, "new_seq": "CGT"},
                         fields(convert(100, "A", "ACGT")))

    def test_the_deletion_position_rule(self):
        """gdtools puts a deletion at `POS + len(ALT)`, and on canonical input so do we.

        Trimming reaches the same answer by a different route, which is the point: the two
        agree wherever the VCF was left-anchored and normalised, and diverge only where it was
        not -- which is exactly the input a non-breseq caller produces.
        """
        for position, ref, alt, expected_position, expected_size in (
                (200, "AC", "A", 201, 1),
                (200, "ACGT", "AC", 202, 2),
                (200, "ACGTAA", "ACG", 203, 3)):
            self.assertEqual(
                {"type": "DEL", "position": expected_position, "size": expected_size},
                fields(convert(position, ref, alt)),
                "disagreed with gdtools on %s -> %s" % (ref, alt))

    def test_breseqs_own_two_mutations(self):
        """breseq wrote a .gd and a .vcf for the same run; the VCF must give back the .gd.

        This is the anchor claim in miniature: `SNP testref 5000 G` and `DEL testref 12000 6`
        are what its `output.gd` holds, and its `output.vcf` spells them as below.
        """
        self.assertEqual({"type": "SNP", "position": 5000, "new_seq": "G"},
                         fields(convert(5000, "T", "G")))
        self.assertEqual({"type": "DEL", "position": 12000, "size": 6},
                         fields(convert(11999, "GAGAATC", "G")))


class DivergenceTestCase(SimpleTestCase):
    """Where we deliberately do something else, and why."""

    def test_a_partial_prefix_is_a_substitution_not_an_insertion(self):
        """`AC -> AGG` is `SUB size=1 new_seq=GG`; gdtools says `INS AGG`.

        gdtools strips only an exact full-REF prefix, so it sees ALT as wholly inserted. What
        the line says is that the `C` at POS+1 *becomes* `GG` -- the C is changed, not merely
        inserted after, and calling it an insertion loses that.
        """
        self.assertEqual({"type": "SUB", "position": 201, "size": 1, "new_seq": "GG"},
                         fields(convert(200, "AC", "AGG")))

    def test_an_equal_length_mnp_is_a_sub(self):
        # gdtools drops these with "Can't classify line" and imports nothing.
        self.assertEqual({"type": "SUB", "position": 100, "size": 4, "new_seq": "TGCA"},
                         fields(convert(100, "ACGT", "TGCA")))

    def test_an_unequal_non_indel_is_a_sub(self):
        """gdtools answers `DEL 203 3` here, which is not what the line says at all.

        `ACGTAA -> TTT` shares no prefix and no suffix: six bases become three. Its length
        rule reaches for DEL because REF is longer, and then puts it in the wrong place.
        """
        self.assertEqual({"type": "SUB", "position": 200, "size": 6, "new_seq": "TTT"},
                         fields(convert(200, "ACGTAA", "TTT")))

    def test_a_one_base_ref_with_an_unrelated_alt_is_a_sub(self):
        # gdtools: INS testref 200 TCGT -- the whole ALT, because it does not start with REF.
        self.assertEqual({"type": "SUB", "position": 200, "size": 1, "new_seq": "TCGT"},
                         fields(convert(200, "A", "TCGT")))

    def test_an_indel_in_a_repeat_is_left_aligned(self):
        """`AAA -> AA` is a deletion of one A and which one is unknowable; take the leftmost.

        Suffix-before-prefix is what does it. The other order gives `DEL 102`, which produces
        the same sequence and agrees with nothing else -- and `start_position` is one of the
        seven `get_or_create` fields, so the same deletion from two callers would fork.
        """
        self.assertEqual({"type": "DEL", "position": 100, "size": 1},
                         fields(convert(100, "AAA", "AA")))

    def test_a_variant_that_changes_nothing_is_refused(self):
        with self.assertRaises(vcf.LineProblem):
            convert(100, "ACGT", "ACGT")


class TrimTestCase(SimpleTestCase):
    def test_trimming_is_idempotent(self):
        for position, ref, alt in ((100, "ACGT", "A"), (100, "A", "ACGT"),
                                   (100, "AC", "AGG"), (5000, "T", "G")):
            once = vcf.trim(position, ref, alt)
            self.assertEqual(once, vcf.trim(*once))

    def test_the_conversion_reproduces_what_the_line_asserted(self):
        """The property that catches the whole class of off-by-one.

        For random REF/ALT pairs against a real sequence: applying the GenomeDiff record we
        produced must give exactly the sequence the VCF line said it would. A conversion whose
        fields look plausible and whose position is one out passes every example test somebody
        thought to write and fails this.
        """
        rng = random.Random(11)
        reference = "".join(rng.choice("ACGT") for _ in range(400))
        checked = 0

        for _ in range(3000):
            position = rng.randint(2, 380)
            ref_length = rng.randint(1, 6)
            ref = reference[position - 1:position - 1 + ref_length]
            alt = "".join(rng.choice("ACGT") for _ in range(rng.randint(1, 6)))
            if ref == alt:
                continue

            record = convert(position, ref, alt)
            expected = reference[:position - 1] + alt + reference[position - 1 + ref_length:]
            self.assertEqual(expected, vcf.applied_to(reference, record),
                             "%s -> %s at %d gave %s" % (ref, alt, position, record))
            checked += 1

        self.assertGreater(checked, 2000, "the generator produced too few usable cases")


HEADER = """##fileformat=VCFv4.2
##contig=<ID=testref,length=20000>
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\ts2
"""


def parse(body, header=HEADER):
    return vcf.read(io.StringIO(header + body))


class ReadTestCase(SimpleTestCase):
    def test_the_header_is_kept_verbatim(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\tDP=30\tGT\t1\t0\n")
        self.assertEqual(["##fileformat=VCFv4.2", "##contig=<ID=testref,length=20000>"],
                         document.header)
        self.assertEqual(["s1", "s2"], document.sample_names)

    def test_a_line_is_kept_verbatim(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\tDP=30\tGT\t1\t0\n")
        line = document.lines[0]
        self.assertEqual(["testref", "5000", ".", "T", "G", "50", "PASS", "DP=30"], line.fixed)
        self.assertEqual({"s1": "1", "s2": "0"}, line.samples)

    def test_a_bad_line_is_reported_and_the_rest_still_loads(self):
        # The same posture gd_import takes: one unreadable line must not cost the others.
        document = parse("testref\tnotanumber\t.\tT\tG\t50\tPASS\t.\tGT\t1\t0\n"
                         "testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT\t1\t0\n")
        self.assertEqual(1, len(document.lines))
        self.assertEqual(1, len(document.problems))
        self.assertIn("POS is not a number", document.problems[0])

    def test_something_that_is_not_a_vcf_raises(self):
        with self.assertRaises(vcf.VcfError):
            vcf.read(io.StringIO("#=GENOME_DIFF\t1.0\nSNP\t1\t.\ttestref\t5000\tG\n"))

    def test_looks_like_vcf(self):
        self.assertTrue(vcf.looks_like_vcf("##fileformat=VCFv4.2\n#CHROM\n"))
        self.assertTrue(vcf.looks_like_vcf("\n\n##fileformat=VCFv4.1\n"))
        self.assertFalse(vcf.looks_like_vcf("#=GENOME_DIFF\t1.0\n"))
        self.assertFalse(vcf.looks_like_vcf(">contig\nACGT\n"))


class GenotypeTestCase(SimpleTestCase):
    def test_a_multiallelic_line_splits_by_genotype(self):
        """gdtools makes this a fatal error and abandons the file.

        Under haploid it is unambiguous: each sample's GT chooses one allele, and two samples
        choosing differently are two mutations, each called in its own sample.
        """
        document = parse("testref\t5000\t.\tT\tG,A\t50\tPASS\t.\tGT\t1\t2\n")
        line = document.lines[0]

        first = vcf.variants_for(line, "s1")
        second = vcf.variants_for(line, "s2")

        self.assertEqual(["G"], [variant.alt for variant in first])
        self.assertEqual(["A"], [variant.alt for variant in second])

    def test_a_reference_call_is_not_a_mutation(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT\t1\t0\n")
        line = document.lines[0]
        self.assertEqual(1, len(vcf.variants_for(line, "s1")))
        self.assertEqual([], vcf.variants_for(line, "s2"))

    def test_a_no_call_is_not_a_mutation(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT\t./.\t.\n")
        line = document.lines[0]
        self.assertEqual([], vcf.variants_for(line, "s1"))
        self.assertEqual([], vcf.variants_for(line, "s2"))

    def test_a_diploid_genotype_is_read_for_which_alleles_it_names(self):
        # Haploid is the assumption; 1/1 and 1 mean the same thing rather than being refused.
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT\t1/1\t0/1\n")
        line = document.lines[0]
        self.assertEqual(["G"], [v.alt for v in vcf.variants_for(line, "s1")])
        self.assertEqual(["G"], [v.alt for v in vcf.variants_for(line, "s2")])

    def test_a_sites_only_line_asserts_every_alt(self):
        header = ("##fileformat=VCFv4.2\n"
                  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        document = parse("testref\t5000\t.\tT\tG,A\t50\tPASS\t.\n", header=header)
        self.assertTrue(document.is_sites_only)
        self.assertEqual(["G", "A"],
                         [v.alt for v in vcf.variants_for(document.lines[0])])

    def test_a_symbolic_allele_is_refused_by_name(self):
        document = parse("testref\t200\t.\tA\t<DEL>\t50\tPASS\tEND=300\tGT\t1\t0\n")
        with self.assertRaises(vcf.LineProblem) as caught:
            vcf.variants_for(document.lines[0], "s1")
        self.assertIn("symbolic", str(caught.exception))

    def test_a_breakend_is_refused_by_name(self):
        document = parse("testref\t200\t.\tA\tA[testref:300[\t50\tPASS\t.\tGT\t1\t0\n")
        with self.assertRaises(vcf.LineProblem):
            vcf.variants_for(document.lines[0], "s1")


class FrequencyTestCase(SimpleTestCase):
    def test_the_samples_own_af_wins(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\tAF=0.9\tGT:AF\t1:0.25\t0:0\n")
        self.assertEqual(0.25, vcf.frequency_for(document.lines[0], "s1", 1))

    def test_allele_depth_is_used_when_there_is_no_af(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT:AD\t1:3,9\t0:10,0\n")
        self.assertEqual(0.75, vcf.frequency_for(document.lines[0], "s1", 1))

    def test_the_sites_af_is_the_last_resort(self):
        # A site-level AF is a property of the cohort rather than of this sample.
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\tAF=0.4\tGT\t1\t0\n")
        self.assertEqual(0.4, vcf.frequency_for(document.lines[0], "s1", 1))

    def test_nothing_says_is_none(self):
        # gd_import._coerce_frequency turns None into 1.0, which is the right reading of a
        # haploid call with no frequency attached.
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT\t1\t0\n")
        self.assertIsNone(vcf.frequency_for(document.lines[0], "s1", 1))

    def test_a_percentage_is_not_read_as_a_fraction(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\tAF=100\tGT\t1\t0\n")
        self.assertIsNone(vcf.frequency_for(document.lines[0], "s1", 1))

    def test_a_multiallelic_af_is_taken_per_allele(self):
        document = parse("testref\t5000\t.\tT\tG,A\t50\tPASS\tAF=0.6,0.3\tGT\t1\t2\n")
        line = document.lines[0]
        self.assertEqual(0.6, vcf.frequency_for(line, "s1", 1))
        self.assertEqual(0.3, vcf.frequency_for(line, "s2", 2))


class InfoTestCase(SimpleTestCase):
    def test_info_parses_including_valueless_flags(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\tDP=30;SOMATIC;AF=0.5\tGT\t1\t0\n")
        self.assertEqual({"DP": "30", "SOMATIC": True, "AF": "0.5"},
                         document.lines[0].info_map())

    def test_an_empty_info_is_an_empty_map(self):
        document = parse("testref\t5000\t.\tT\tG\t50\tPASS\t.\tGT\t1\t0\n")
        self.assertEqual({}, document.lines[0].info_map())

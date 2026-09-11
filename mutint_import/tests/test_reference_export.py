"""`reference_export`: a reference, or some of its contigs, rendered for download.

The GenBank writer is the half worth testing hard. Nothing else in the suite writes GenBank,
and what it writes is judged by one thing: whether the suite's own GenBank *reader* loads it
back to the model it was written from.
"""

import os
import shutil
import tempfile
import warnings

from django.test import SimpleTestCase

from mutint_import import reference, reference_export
from mutint_import.annotate import genbank, gff3
from mutint_import.annotate.model import LoadedReferenceSequences

CHROMOSOME = "chr"
PLASMID = "NZ_CP0123456789.1_plasmid"  # over GenBank's 16-character LOCUS allowance, dotted

SEQUENCE_A = "ACGTTGCA" * 50  # 400 bases
SEQUENCE_B = "GGATCCAA" * 20  # 160 bases


def _fasta(seq_id, sequence):
    lines = [">" + seq_id]
    for offset in range(0, len(sequence), 70):
        lines.append(sequence[offset:offset + 70])
    return lines


# Every shape the model can hold, in breseq's own dialect (what the store keeps):
# a named CDS, one known by its locus tag alone, a pseudogene (fCDS), a minus-strand spliced
# CDS with an indeterminate 3' end, a tRNA, a repeat with an indeterminate start, and a
# mobile element -- across two contigs.
GFF3_TEXT = "\n".join(
    ["##gff-version 3",
     "##sequence-region\t%s\t1\t%d" % (CHROMOSOME, len(SEQUENCE_A)),
     "##sequence-region\t%s\t1\t%d" % (PLASMID, len(SEQUENCE_B)),
     "\t".join([CHROMOSOME, "mutint", "CDS", "10", "60", ".", "+", "0",
                "Alias=b0001;ID=b0001;Name=thrA;Note=aspartokinase;transl_table=11"]),
     "\t".join([CHROMOSOME, "mutint", "CDS", "70", "120", ".", "+", "0",
                "Alias=b0002;ID=b0002;Note=hypothetical protein;transl_table=11"]),
     "\t".join([CHROMOSOME, "mutint", "fCDS", "130", "180", ".", "+", "0",
                "Alias=b0003;ID=b0003;Name=yaaX;Note=pseudo;Pseudo=true;transl_table=11"]),
     "\t".join([CHROMOSOME, "mutint", "CDS", "250", "300", ".", "-", "0",
                "Alias=b0004;ID=b0004;Name=spl;Note=spliced;transl_table=11"]),
     "\t".join([CHROMOSOME, "mutint", "CDS", "200", "230", ".", "-", "0",
                "Alias=b0004;ID=b0004;Name=spl;Note=spliced;transl_table=11;"
                "indeterminate_coordinate=start"]),
     "\t".join([CHROMOSOME, "mutint", "tRNA", "310", "380", ".", "+", "0",
                "Alias=b0005;ID=b0005;Name=thrW;Note=tRNA-Thr"]),
     "\t".join([PLASMID, "mutint", "repeat_region", "5", "40", ".", "+", "0",
                "Name=REP1;Note=repeat region;indeterminate_coordinate=start"]),
     "\t".join([PLASMID, "mutint", "mobile_element", "50", "150", ".", "-", "0",
                "Name=IS1;Note=insertion sequence:IS1"]),
     "##FASTA"]
    + _fasta(CHROMOSOME, SEQUENCE_A) + _fasta(PLASMID, SEQUENCE_B)) + "\n"


class _Loaded(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.refs = gff3.load_gff3(self._write("ref.gff3", GFF3_TEXT))

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def _feature(self, refs, seq_id, name):
        for feature in refs[seq_id].features:
            if feature.name == name:
                return feature
        self.fail("no feature %s on %s" % (name, seq_id))


class SubsetTestCase(_Loaded):
    def test_nothing_named_means_every_contig(self):
        chosen = reference_export.subset(self.refs, [])
        self.assertEqual(sorted(chosen.seq_ids()), sorted([CHROMOSOME, PLASMID]))

    def test_some_named_means_exactly_those(self):
        chosen = reference_export.subset(self.refs, [PLASMID])
        self.assertEqual(chosen.seq_ids(), [PLASMID])
        self.assertIs(chosen[PLASMID], self.refs[PLASMID])

    def test_a_repeated_id_is_taken_once(self):
        chosen = reference_export.subset(self.refs, [PLASMID, PLASMID])
        self.assertEqual(chosen.seq_ids(), [PLASMID])

    def test_an_alias_selects_the_contig_once_under_its_own_name(self):
        self.refs.sequences["alias"] = self.refs[CHROMOSOME]
        chosen = reference_export.subset(self.refs, ["alias", CHROMOSOME])
        self.assertEqual(chosen.seq_ids(), [CHROMOSOME])

    def test_an_unknown_id_is_refused_by_name(self):
        with self.assertRaises(reference_export.UnknownSequence) as caught:
            reference_export.subset(self.refs, [PLASMID, "nope", "zzz"])
        self.assertIn("nope, zzz", str(caught.exception))

    def test_the_source_is_left_alone(self):
        reference_export.subset(self.refs, [PLASMID])
        self.assertEqual(sorted(self.refs.seq_ids()), sorted([CHROMOSOME, PLASMID]))

    def test_is_complete(self):
        self.assertTrue(reference_export.is_complete(
            self.refs, reference_export.subset(self.refs, [])))
        self.assertFalse(reference_export.is_complete(
            self.refs, reference_export.subset(self.refs, [PLASMID])))


class FastaAndGff3TestCase(_Loaded):
    def test_fasta_is_the_canonical_rendering(self):
        self.assertEqual(reference_export.render(self.refs, "fasta"),
                         reference.render_fasta(reference.sequences_of(self.refs)))

    def test_fasta_of_a_subset_holds_only_that_contig(self):
        text = reference_export.render(reference_export.subset(self.refs, [PLASMID]), "fasta")
        self.assertIn(">" + PLASMID + "\n", text)
        self.assertNotIn(">" + CHROMOSOME, text)

    def test_gff3_of_everything_is_the_stored_form(self):
        self.assertEqual(reference_export.render(self.refs, "gff3"),
                         gff3.render_breseq_gff3(self.refs))

    def test_gff3_of_a_subset_reloads_to_that_contig_alone(self):
        text = reference_export.render(reference_export.subset(self.refs, [PLASMID]), "gff3")
        reloaded = gff3.load_gff3(self._write("subset.gff3", text))
        self.assertEqual(reloaded.seq_ids(), [PLASMID])
        self.assertEqual(len(reloaded[PLASMID].features), 2)

    def test_an_unknown_format_is_a_key_error(self):
        with self.assertRaises(KeyError):
            reference_export.render(self.refs, "xlsx")


class GenBankTestCase(_Loaded):
    def _round_trip(self, refs=None):
        text = reference_export.render(refs or self.refs, "genbank")
        return text, genbank.load_genbank(self._write("out.gbk", text))

    def test_it_reads_back_to_the_model_it_was_written_from(self):
        """The property everything else here is a detail of: the suite's own GenBank
        reader loads what the writer wrote to the same features, so the two render to the
        same canonical GFF3."""
        _, reloaded = self._round_trip()
        self.assertEqual(gff3.render_breseq_gff3(reloaded), gff3.render_breseq_gff3(self.refs))

    def test_a_spliced_minus_strand_gene_keeps_its_parts_in_order(self):
        _, reloaded = self._round_trip()
        feature = self._feature(reloaded, CHROMOSOME, "spl")
        self.assertEqual([(l.start_1, l.end_1, l.strand) for l in feature.locations],
                         [(250, 300, -1), (200, 230, -1)])
        # The marker is on the genomic start of the last part, which on the minus strand
        # is the gene's own 3' end -- what the stranded feature-level question asks.
        self.assertFalse(feature.locations[0].start_is_indeterminate)
        self.assertTrue(feature.locations[1].start_is_indeterminate)
        self.assertTrue(feature.end_is_indeterminate())
        self.assertFalse(feature.start_is_indeterminate())

    def test_pseudo_and_translation_table_survive(self):
        _, reloaded = self._round_trip()
        self.assertTrue(self._feature(reloaded, CHROMOSOME, "yaaX").pseudogene)
        self.assertFalse(self._feature(reloaded, CHROMOSOME, "thrA").pseudogene)
        self.assertEqual(self._feature(reloaded, CHROMOSOME, "thrA").translation_table, 11)
        self.assertEqual(self._feature(reloaded, CHROMOSOME, "thrA").locus_tag, "b0001")

    def test_repeats_keep_their_family_names(self):
        _, reloaded = self._round_trip()
        names = sorted((f.type, f.name) for f in reloaded[PLASMID].features)
        self.assertEqual(names, [("mobile_element", "IS1"), ("repeat_region", "REP1")])
        self.assertTrue(self._feature(reloaded, PLASMID, "REP1")
                        .locations[0].start_is_indeterminate)

    def test_the_dotted_long_contig_name_is_the_locus(self):
        text, reloaded = self._round_trip()
        self.assertIn(PLASMID, reloaded.seq_ids())
        self.assertIn("LOCUS       " + PLASMID, text)

    def test_the_text_is_what_a_genbank_reader_expects(self):
        text, _ = self._round_trip()
        self.assertIn('/mobile_element_type="insertion sequence:IS1"', text)
        self.assertIn('/rpt_family="REP1"', text)
        self.assertIn("complement(join(<200..230,250..300))", text)
        self.assertIn("<5..40", text)
        self.assertIn("\n                     /pseudo\n", text)
        self.assertIn('/gene="thrA"', text)
        self.assertIn('/transl_table=11', text)

    def test_a_locus_tag_is_not_written_as_a_gene_name(self):
        """The GFF3 loader names a nameless gene by its locus tag. Writing that back as
        /gene would mint a gene symbol the annotation never had."""
        text, reloaded = self._round_trip()
        self.assertNotIn('/gene="b0002"', text)
        self.assertIn('/locus_tag="b0002"', text)
        self.assertEqual(self._feature(reloaded, CHROMOSOME, "b0002").locus_tag, "b0002")

    def test_the_definition_is_the_experiment_name(self):
        text = reference_export.render(self.refs, "genbank", definition="My experiment")
        self.assertIn("DEFINITION  My experiment", text)

    def test_a_long_locus_name_raises_no_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reference_export.render_genbank(self.refs)
        self.assertEqual([str(w.message) for w in caught], [])

    def test_a_contig_with_no_features_writes_an_empty_feature_table(self):
        refs = LoadedReferenceSequences()
        refs.add(gff3.AnnotatedSequence("bare", "ACGT" * 10))
        text, reloaded = self._round_trip(refs)
        self.assertEqual(reloaded.seq_ids(), ["bare"])
        self.assertEqual(reloaded["bare"].sequence, "ACGT" * 10)


class FilenameTestCase(SimpleTestCase):
    def test_the_whole_reference(self):
        self.assertEqual(reference_export.filename("exp", ["a", "b"], "fasta", True),
                         "exp_reference.fasta")

    def test_one_contig_is_named_for_it_and_keeps_its_dots(self):
        self.assertEqual(reference_export.filename("exp", ["NC_000913.3"], "genbank", False),
                         "exp_NC_000913.3.gbk")

    def test_several_are_counted(self):
        self.assertEqual(reference_export.filename("exp", ["a", "b"], "gff3", False),
                         "exp_2_sequences.gff3")

    def test_whitespace_and_separators_become_underscores(self):
        self.assertEqual(
            reference_export.filename("LTEE Ara-1 / 2000 gen", ["a"], "fasta", True),
            "LTEE_Ara-1_2000_gen_reference.fasta")

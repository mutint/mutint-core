"""
Annotating from breseq's GFF3.

A breseq folder carries `data/reference.gff3`, not GenBank, so that is the usual
annotation input. fixtures/synthetic.gff3 is what breseq itself produced from
fixtures/synthetic.gbk:

    breseq CONVERT-REFERENCE -f GFF3 -o synthetic.gff3 synthetic.gbk

so the two describe the same genome. Annotating the same GD against each must
therefore give the same answer -- which is the real test here, because the GFF3
expresses spliced genes, pseudogenes and indeterminate ends completely
differently from GenBank.
"""

import os

from django.test import SimpleTestCase

from aledb_import.annotate.annotator import annotate_mutations
from aledb_import.annotate.gff3 import load_gff3, parse_attributes, unescape
from aledb_import.annotate.loader import (
    FORMAT_GENBANK,
    FORMAT_GFF3,
    UnsupportedReferenceFormat,
    detect_format,
    load_reference,
)
from aledb_import.gdparse.gdparse import gdparse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
GENBANK = os.path.join(FIXTURES, 'synthetic.gbk')
GFF3 = os.path.join(FIXTURES, 'synthetic.gff3')


def _load_mutations():
    with open(os.path.join(FIXTURES, 'synthetic.gd'), 'rb') as handle:
        parser = gdparse.GDParser(file_handle=handle)
    return [parser.data['mutation'][key] for key in sorted(parser.data['mutation'])]


def _annotate_with(path):
    mutations = _load_mutations()
    annotate_mutations(mutations, load_reference(path))
    return mutations


class Gff3MatchesGenbankTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.from_genbank = _annotate_with(GENBANK)
        cls.from_gff3 = _annotate_with(GFF3)

    def test_the_same_mutations_are_annotated(self):
        self.assertEqual(len(self.from_genbank), len(self.from_gff3))
        self.assertGreaterEqual(len(self.from_gff3), 30)

    def test_annotation_is_identical(self):
        differences = []
        for from_genbank, from_gff3 in zip(self.from_genbank, self.from_gff3):
            keys = set(from_genbank) | set(from_gff3)
            for key in sorted(keys):
                want = str(from_genbank.get(key, ''))
                got = str(from_gff3.get(key, ''))
                if want != got:
                    differences.append(
                        '%s %s:%s %s: genbank=%r gff3=%r'
                        % (from_genbank.get('type'), from_genbank.get('seq_id'),
                           from_genbank.get('position'), key, want, got))
        self.assertEqual([], differences, '\n'.join(differences))


class Gff3ReaderTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.references = load_gff3(GFF3)

    def gene_named(self, name):
        for feature in self.references['SYN001'].features:
            if feature.name == name:
                return feature
        raise AssertionError('no feature named %r' % name)

    def test_sequence_is_read_from_the_inline_fasta(self):
        self.assertEqual(6000, len(self.references['SYN001']))

    def test_spliced_gene_keeps_its_sublocations(self):
        # breseq writes a spliced CDS as several rows sharing one ID.
        spliced = self.gene_named('splA')
        self.assertEqual(2, len(spliced.locations))
        self.assertEqual([(2501, 2600), (2701, 2801)],
                         [(loc.start_1, loc.end_1) for loc in spliced.locations])

    def test_minus_strand_spliced_gene_keeps_gene_order(self):
        # Row order is 5'->3' along the gene, which for a minus-strand gene runs
        # backwards along the genome. Reordering it would shift every codon.
        spliced = self.gene_named('splB')
        self.assertEqual([(3201, 3300), (3001, 3101)],
                         [(loc.start_1, loc.end_1) for loc in spliced.locations])
        self.assertEqual([-1, -1], [loc.strand for loc in spliced.locations])

    def test_fcds_becomes_a_pseudo_cds(self):
        pseudo = self.gene_named('pseuA')
        self.assertEqual('CDS', pseudo.type)
        self.assertTrue(pseudo.pseudogene)

    def test_translation_table_is_read(self):
        self.assertEqual(11, self.gene_named('thrA').translation_table)

    def test_locus_tag_comes_from_the_alias(self):
        self.assertEqual('b0001', self.gene_named('thrA').locus_tag)

    def test_product_comes_from_the_note(self):
        self.assertEqual('aspartokinase I', self.gene_named('thrA').product)

    def test_indeterminate_coordinates_are_read(self):
        self.assertTrue(self.gene_named('fuzA').start_is_indeterminate())
        self.assertTrue(self.gene_named('fuzB').end_is_indeterminate())

    def test_non_coding_types_are_kept_distinct(self):
        self.assertEqual('tRNA', self.gene_named('trnA').type)

    def test_repeat_regions_are_loaded(self):
        self.assertEqual(2, len(self.references['SYN001'].repeat_locations))
        self.assertEqual(200, len(self.references.repeat_family_sequence('IS150', 1)))


class Gff3EscapingTest(SimpleTestCase):

    def test_percent_decoding(self):
        self.assertEqual('spliced gene, plus strand', unescape('spliced gene%2C plus strand'))
        self.assertEqual('a;b=c', unescape('a%3Bb%3Dc'))

    def test_percent_itself_decodes_last(self):
        # %253B must come back as the literal text "%3B", not as ";".
        self.assertEqual('%3B', unescape('%253B'))

    def test_values_split_on_comma_before_unescaping(self):
        attributes = parse_attributes('Name=a,b;Note=one%2Ctwo')
        self.assertEqual(['a', 'b'], attributes['Name'])
        self.assertEqual(['one,two'], attributes['Note'])


class FormatDetectionTest(SimpleTestCase):

    def test_genbank_by_suffix_and_by_content(self):
        self.assertEqual(FORMAT_GENBANK, detect_format(GENBANK))
        self.assertEqual(FORMAT_GENBANK, detect_format(GENBANK, original_name='ref.unknown'))

    def test_gff3_by_suffix_and_by_content(self):
        self.assertEqual(FORMAT_GFF3, detect_format(GFF3))
        self.assertEqual(FORMAT_GFF3, detect_format(GFF3, original_name='ref.unknown'))

    def test_a_bare_fasta_is_rejected(self):
        # A FASTA has no gene features, so annotating against it would silently
        # produce blank gene names for everything.
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.fasta', delete=False) as handle:
            handle.write('>SYN001\nACGT\n')
            path = handle.name
        self.addCleanup(os.unlink, path)
        with self.assertRaises(UnsupportedReferenceFormat):
            detect_format(path)

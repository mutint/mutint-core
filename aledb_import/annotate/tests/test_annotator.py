"""
Golden test: our annotation must equal what `gdtools ANNOTATE` produces.

fixtures/synthetic.expected.gd was generated once by the real breseq toolchain:

    python aledb_import/annotate/tests/make_fixtures.py
    gdtools ANNOTATE -f GD --add-html-fields \
        -r aledb_import/annotate/tests/fixtures/synthetic.gbk \
        -o aledb_import/annotate/tests/fixtures/synthetic.expected.gd \
        aledb_import/annotate/tests/fixtures/synthetic.gd

so this test needs neither breseq nor a network. If it fails, our annotator has
drifted from breseq -- not the other way round.
"""

import os

from django.test import SimpleTestCase

from aledb_import.annotate.annotator import annotate_mutations
from aledb_import.annotate.display import add_html_fields, text_from_html
from aledb_import.annotate.loader import load_reference
from aledb_import.gdparse.gdparse import gdparse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

# gdtools writes these; they are bookkeeping rather than annotation.
IGNORED_KEYS = {'parent_ids', 'type'}


def _load_gd(name):
    with open(os.path.join(FIXTURES, name), 'rb') as handle:
        return gdparse.GDParser(file_handle=handle)


def _identity(mutation):
    """gdtools re-sorts and renumbers, so match on coordinates, not on GD id."""
    return (mutation.get('type'), mutation.get('seq_id'), mutation.get('position'),
            mutation.get('size', ''), mutation.get('new_seq', ''),
            mutation.get('repeat_name', ''), mutation.get('new_copy_number', ''),
            mutation.get('strand', ''))


class AnnotationMatchesGdtoolsTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.references = load_reference(os.path.join(FIXTURES, 'synthetic.gbk'))

        mutations = list(_load_gd('synthetic.gd').data['mutation'].values())
        annotate_mutations(mutations, cls.references)
        for mutation in mutations:
            add_html_fields(mutation)
        cls.actual = {_identity(m): m for m in mutations}
        cls.expected = {_identity(m): m
                        for m in _load_gd('synthetic.expected.gd').data['mutation'].values()}

    def test_every_fixture_mutation_was_annotated(self):
        self.assertEqual(sorted(map(str, self.expected)), sorted(map(str, self.actual)))
        self.assertGreaterEqual(len(self.expected), 30)

    def test_annotation_fields_match_gdtools(self):
        differences = []
        for identity, expected in self.expected.items():
            actual = self.actual[identity]
            for key in sorted((set(expected) | set(actual)) - IGNORED_KEYS):
                want = str(expected.get(key, ''))
                # gdtools omits empty values when it writes a GD line, so an
                # absent key and an empty string mean the same thing.
                got = str(actual.get(key, ''))
                if want != got:
                    differences.append('%s %s:%s %s: gdtools=%r ours=%r'
                                       % (expected.get('type'), expected.get('seq_id'),
                                          expected.get('position'), key, want, got))
        self.assertEqual([], differences, '\n'.join(differences))

    def test_covers_every_mutation_type(self):
        types = {mutation.get('type') for mutation in self.expected.values()}
        self.assertEqual({'SNP', 'SUB', 'DEL', 'INS', 'MOB', 'AMP', 'CON', 'INV'}, types)

    def test_covers_every_snp_type(self):
        snp_types = set()
        for mutation in self.expected.values():
            for snp_type in str(mutation.get('snp_type', '')).split('|'):
                if snp_type:
                    snp_types.add(snp_type)
        self.assertLessEqual(
            {'synonymous', 'nonsynonymous', 'nonsense', 'noncoding', 'pseudogene',
             'intergenic'},
            snp_types)


class AnnotationDetailTest(SimpleTestCase):
    """
    Spot checks on the cases that are easiest to get subtly wrong, expressed as
    readable assertions rather than as a wall of golden text.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.references = load_reference(os.path.join(FIXTURES, 'synthetic.gbk'))
        mutations = list(_load_gd('synthetic.gd').data['mutation'].values())
        annotate_mutations(mutations, cls.references)
        for mutation in mutations:
            add_html_fields(mutation)
        cls.by_position = {}
        for mutation in mutations:
            cls.by_position.setdefault(
                (mutation.get('type'), mutation.get('position')), mutation)

    def mutation(self, mutation_type, position):
        return self.by_position[(mutation_type, position)]

    def test_coding_snp_gets_a_bare_gene_position(self):
        # Only non-SNPs get the "coding (n/N nt)" form.
        self.assertEqual('30', self.mutation('SNP', 130)['gene_position'])
        self.assertEqual('nonsense', self.mutation('SNP', 130)['snp_type'])
        self.assertEqual('snp_nonsense', self.mutation('SNP', 130)['mutation_category'])

    def test_non_snp_in_cds_gets_coding_form(self):
        self.assertTrue(self.mutation('INS', 200)['gene_position'].startswith('coding ('))

    def test_intergenic_sign_encodes_gene_orientation(self):
        # thrA-> ... <-thrB : downstream of both, so both signs are "+".
        self.assertEqual('intergenic (+50/+51)', self.mutation('SNP', 450)['gene_position'])
        self.assertEqual('thrA/thrB', self.mutation('SNP', 450)['gene_name'])

    def test_convergent_neighbours_have_no_promoter(self):
        self.assertEqual('', self.mutation('SNP', 450)['genes_promoter'])

    def test_divergent_neighbours_put_both_genes_in_promoter(self):
        self.assertEqual('divA,divB', self.mutation('SNP', 2050)['genes_promoter'])

    def test_promoter_ignores_genes_beyond_the_cutoff(self):
        # The nearest gene facing this position (fuzA, on the plus strand) starts
        # 501 bp away, well past the 150 bp promoter distance.
        self.assertEqual('', self.mutation('INS', 4500)['genes_promoter'])

    def test_promoter_ignores_a_close_but_wrongly_oriented_gene(self):
        # thrB ends 100 bp to the left and is transcribed leftwards, so its
        # promoter is affected; araJ is 101 bp to the right but also transcribed
        # leftwards, so the mutation sits behind it, not in front of it.
        self.assertEqual('thrB', self.mutation('SNP', 900)['genes_promoter'])

    def test_spanning_deletion_puts_gene_names_in_gene_product(self):
        deletion = self.mutation('DEL', 90)
        self.assertEqual('thrA–[araJ]', deletion['gene_name'])
        self.assertEqual('thrA,thrB,[araJ]', deletion['gene_product'])
        self.assertEqual('thrA,thrB', deletion['genes_inactivated'])
        self.assertEqual('large_deletion', deletion['mutation_category'])

    def test_snp_in_two_genes_joins_per_gene_values_with_pipe(self):
        overlapping = self.mutation('SNP', 5600)
        self.assertEqual(2, len(overlapping['gene_name'].split('|')))
        self.assertEqual(2, len(overlapping['snp_type'].split('|')))

    def test_minus_strand_codon_is_read_in_gene_orientation(self):
        minus_strand = self.mutation('SNP', 1595)
        self.assertEqual('<', minus_strand['gene_strand'])
        self.assertEqual(3, len(minus_strand['codon_ref_seq']))

    def test_pseudogene_and_noncoding_are_labelled(self):
        self.assertEqual('pseudogene', self.mutation('SNP', 3750)['snp_type'])
        self.assertEqual('noncoding', self.mutation('SNP', 3520)['snp_type'])

    def test_list_to_entry_blanks_all_na_fields(self):
        # A tRNA SNP has no codon, so the codon fields collapse to empty rather
        # than being written out as "NA".
        self.assertEqual('', self.mutation('SNP', 3520)['codon_ref_seq'])

    def test_mob_records_the_repeat_family_size(self):
        self.assertEqual('200', self.mutation('MOB', 1300)['repeat_size'])

    def test_long_spans_record_a_length_instead_of_a_sequence(self):
        self.assertEqual('1000-bp', self.mutation('DEL', 90)['ref_seq'])
        self.assertEqual('C', self.mutation('SNP', 130)['ref_seq'])


class DisplayTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        references = load_reference(os.path.join(FIXTURES, 'synthetic.gbk'))
        mutations = list(_load_gd('synthetic.gd').data['mutation'].values())
        annotate_mutations(mutations, references)
        for mutation in mutations:
            add_html_fields(mutation)
        cls.by_position = {}
        for mutation in mutations:
            cls.by_position.setdefault(
                (mutation.get('type'), mutation.get('position')), mutation)

    def text_of(self, mutation_type, position, key):
        return text_from_html(self.by_position[(mutation_type, position)][key])

    def test_snp_mutation_text(self):
        self.assertEqual('C→A', self.text_of('SNP', 130, 'html_mutation'))

    def test_deletion_mutation_text(self):
        self.assertEqual('Δ1,000 bp', self.text_of('DEL', 90, 'html_mutation'))

    def test_insertion_mutation_text(self):
        self.assertEqual('+G', self.text_of('INS', 200, 'html_mutation'))

    def test_amplification_annotation_text(self):
        self.assertEqual('amplification', self.text_of('AMP', 1750, 'html_mutation_annotation'))

    def test_coding_snp_annotation_text(self):
        self.assertEqual('Y10* (TAC→TAA)', self.text_of('SNP', 130, 'html_mutation_annotation'))

    def test_intergenic_annotation_text(self):
        self.assertEqual('intergenic (+50/+51)',
                         self.text_of('SNP', 450, 'html_mutation_annotation'))

    def test_mediated_deletion_annotation_text(self):
        # breseq renders these through nonbreaking(), which turns "-" into
        # &#8209;. Stripping the tags therefore leaves a non-breaking hyphen
        # (U+2011), not an ASCII one -- which is exactly what ALEdb has always
        # stored in Mutation.protein_change.
        self.assertEqual('IS150\u2011mediated',
                         self.text_of('DEL', 1400, 'html_mutation_annotation'))

    def test_negative_intergenic_offset_uses_a_nonbreaking_hyphen(self):
        self.assertEqual('intergenic (\u201150/+151)',
                         self.text_of('SNP', 850, 'html_mutation_annotation'))

    def test_gene_name_html_carries_strand_arrows(self):
        html = self.by_position[('SNP', 130)]['html_gene_name']
        self.assertIn('<i>thrA</i>', html)
        self.assertIn('&rarr;', html)

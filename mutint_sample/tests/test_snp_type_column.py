"""The SNP type column: breseq's `snp_type` as a column of both mutation tables.

`snp_type_cells` is the one writer, so most of this is about it; the two page tests pin that
the column is where the Filter tab's shared rules expect it on each table.
"""

from django.test import TestCase

from mutint_sample import mutation_matrix
from mutint_sample.breseq_report import snp_type_cells
from mutint_sample.models import Mutation
from mutint_sample.tests.test_mutation_matrix import _Fixture
from mutint_sample.util import get_all_calls_filtered


class SnpTypeCellsTestCase(TestCase):
    def test_a_non_snp_has_an_empty_cell_and_an_empty_twin(self):
        for value in ("", None):
            self.assertEqual({"snp_type": "", "snp_type_text": ""}, snp_type_cells(value))

    def test_a_coding_token_is_coloured_by_the_annotation_cells_class(self):
        self.assertEqual(
            {"snp_type": '<span class="snp_type_nonsynonymous">nonsynonymous</span>',
             "snp_type_text": "nonsynonymous"},
            snp_type_cells("nonsynonymous"))
        self.assertIn('class="snp_type_nonsense"', snp_type_cells("nonsense")["snp_type"])
        self.assertIn('class="snp_type_synonymous"', snp_type_cells("synonymous")["snp_type"])

    def test_a_non_coding_token_is_plain_words(self):
        for token in ("intergenic", "noncoding", "pseudogene"):
            self.assertEqual({"snp_type": token, "snp_type_text": token}, snp_type_cells(token))

    def test_a_compound_value_reads_as_a_list(self):
        cells = snp_type_cells("nonsynonymous|synonymous")
        self.assertEqual("nonsynonymous, synonymous", cells["snp_type_text"])
        self.assertEqual('<span class="snp_type_nonsynonymous">nonsynonymous</span>, '
                         '<span class="snp_type_synonymous">synonymous</span>',
                         cells["snp_type"])

    def test_markup_in_a_value_is_escaped(self):
        cells = snp_type_cells("<b>x</b>")
        self.assertNotIn("<b>", cells["snp_type"])
        self.assertEqual("<b>x</b>", cells["snp_type_text"])


class SnpTypeColumnTestCase(_Fixture):
    def test_the_column_sits_after_mutation_and_shows_by_default(self):
        keys = [c.key for c in mutation_matrix.DESCRIPTIVE]
        self.assertEqual(keys.index("mutation") + 1, keys.index("snp_type"))
        self.assertEqual(keys.index("snp_type") + 1, keys.index("annotation"))
        self.assertTrue(mutation_matrix.SNP_TYPE_COLUMN.default_visible)

    def test_a_rows_cell_is_its_mutations_snp_type_with_a_text_twin(self):
        mutation = Mutation.objects.get(start_position=100)
        mutation.annotation = dict(mutation.annotation, snp_type="synonymous")
        mutation.snp_type = "synonymous"
        mutation.save()
        self.calls = get_all_calls_filtered(self.experiment.id)
        row = self.row(self.matrix(), 100)
        self.assertEqual("synonymous", row["snp_type_text"])
        self.assertEqual('<span class="snp_type_synonymous">synonymous</span>', row["snp_type"])
        deletion = next(r for r in self.matrix().rows if r["type"] != "SNP")
        self.assertEqual("", deletion["snp_type"])
        self.assertEqual("", deletion["snp_type_text"])

    def test_an_unannotated_mutation_still_reads_the_promoted_column(self):
        mutation = Mutation.objects.get(start_position=100)
        mutation.annotation = None
        mutation.snp_type = "intergenic"
        mutation.save()
        self.calls = get_all_calls_filtered(self.experiment.id)
        row = self.row(self.matrix(), 100)
        self.assertFalse(row["annotated"])
        self.assertEqual("intergenic", row["snp_type"])

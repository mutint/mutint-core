"""What `Mutation.gene` records, and the limit past which it stops recording names.

`gene` is one of the seven fields `Mutation.objects.get_or_create` keys on, so what this
function produces is a database key and not merely a display string. Two things follow, and
both are asserted here: the string for a range under the limit must not move (or every such
mutation forks on the next import), and the string for one over it must equal what
`aledb_seq.0012` writes for the rows already stored.
"""

import unittest

from aledb_common.util import GENE_LIST_LIMIT
from aledb_import.gene_annotation import get_annotated_gene_list


def _range_of(count):
    """breseq's own shape: gene_name is the span, gene_product carries the NAMES."""
    names = ["gene%04d" % index for index in range(count)]
    return "%s–[%s]" % (names[0], names[-1]), ",".join(names)


class GeneAnnotationTestCase(unittest.TestCase):

    def test_an_intergenic_mutation_is_both_flanking_genes(self):
        self.assertEqual(["thrA", "thrB"], get_annotated_gene_list("thrA/thrB", "x, y"))

    def test_a_single_gene(self):
        self.assertEqual(["thrA"], get_annotated_gene_list("thrA", "aspartokinase"))

    def test_a_range_under_the_limit_records_what_it_always_recorded(self):
        """The stored string is a get_or_create key -- moving it forks every range mutation."""
        gene_name, gene_product = _range_of(40)

        self.assertEqual(gene_product,
                         ", ".join(get_annotated_gene_list(gene_name, gene_product)))

    def test_a_range_at_the_limit_is_still_recorded(self):
        gene_name, gene_product = _range_of(GENE_LIST_LIMIT)

        self.assertEqual(gene_product,
                         ", ".join(get_annotated_gene_list(gene_name, gene_product)))

    def test_a_range_over_the_limit_records_the_range_and_not_the_names(self):
        gene_name, gene_product = _range_of(GENE_LIST_LIMIT + 1)
        recorded = get_annotated_gene_list(gene_name, gene_product)

        self.assertEqual([gene_name], recorded)
        self.assertNotIn("gene0500", ", ".join(recorded))

    def test_what_it_records_over_the_limit_is_what_the_migration_writes(self):
        """`aledb_seq.0012` sets `gene` to `annotation['gene_name']`. If these two ever
        disagree, a migrated row stops matching the importer and forks on re-import."""
        gene_name, gene_product = _range_of(4318)

        self.assertEqual(gene_name,
                         ", ".join(get_annotated_gene_list(gene_name, gene_product)))

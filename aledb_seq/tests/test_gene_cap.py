"""Past `GENE_LIST_LIMIT` genes, what gets recorded is the range and not the names.

This was `test_gene_cap_migration`, which drove `aledb_seq.0012` -- the migration that moved
the seven rows written before the cap existed. That migration is gone with the rest of the
history, and the *rule* it repaired the database to is not: it lives in
`get_annotated_gene_list`, which every import goes through.

**Why the rule matters, which is easy to lose along with the migration.** `gene` is one of the
seven fields `Mutation.objects.get_or_create` keys on, so what the importer computes and what
is stored have to be the same string for ever. If they ever differ, re-importing a sample
mints a second `Mutation` for the same biological mutation and splits its observations across
both -- silently. And the string can be enormous: a 4,318-gene inversion produced 23,003
characters against a `CharField(max_length=19000)`, which SQLite accepted and PostgreSQL would
refuse outright.
"""

from django.test import TestCase

from aledb_common.util import GENE_LIST_LIMIT
from aledb_import.gene_annotation import get_annotated_gene_list
from aledb_seq.models import Mutation


def _range_of(count):
    """`(gene_name, gene_product)` as breseq writes them for a range spanning `count` genes."""
    names = ["gene%04d" % index for index in range(count)]
    return "%s–[%s]" % (names[0], names[-1]), ",".join(names)


class GeneListCapTestCase(TestCase):

    def test_over_the_limit_records_the_range(self):
        gene_name, gene_product = _range_of(4318)

        self.assertEqual([gene_name], get_annotated_gene_list(gene_name, gene_product))

    def test_at_the_limit_the_product_is_recorded_unchanged(self):
        """The boundary, because the whole value of a cap is where it falls.

        What comes back is the product string unchanged rather than a list of a thousand
        names, and that is deliberate rather than a near miss: counting splits on
        `GENE_LIST_SPLIT`, which accepts a comma with or without a space, while *producing*
        keeps `GENE_RANGE_ANNOTATION_DELIMITER`. Tidying that would rewrite `Mutation.gene`
        for every range mutation, and `gene` is part of the get_or_create key -- so every one
        of them would fork on the next import.
        """
        gene_name, gene_product = _range_of(GENE_LIST_LIMIT)

        recorded = get_annotated_gene_list(gene_name, gene_product)

        self.assertEqual(gene_product, ", ".join(recorded))
        self.assertNotEqual([gene_name], recorded, "at the limit the names are still kept")

    def test_what_it_records_fits_the_column(self):
        """The reason the cap exists. 23,003 characters into max_length=19000 was accepted by
        SQLite and would have been refused by PostgreSQL -- so the failure was invisible until
        it was fatal."""
        gene_name, gene_product = _range_of(4318)

        stored = ", ".join(get_annotated_gene_list(gene_name, gene_product))

        self.assertLessEqual(len(stored), Mutation._meta.get_field("gene").max_length)

    def test_an_ordinary_range_is_untouched(self):
        self.assertEqual(["thrA", "thrB"], get_annotated_gene_list("thrA–thrB", "thrA, thrB"))

    def test_a_single_gene_is_untouched(self):
        self.assertEqual(["thrA"], get_annotated_gene_list("thrA"))

"""`aledb_seq.0012` moves the gene lists that were recorded before the importer capped them.

Called directly with the real app registry -- every column it touches still exists on the live
model, so none of `test_caller_flag_migration`'s executor machinery is needed and
`aledb_mutation_editor.tests.test_filter_migration` set the precedent for the direct call.

The invariant worth pinning is not that the string got shorter. It is that the string the
migration writes is **the same string the importer now computes**: `gene` is part of
`Mutation.objects.get_or_create`'s key, so if the two ever disagree a re-import of that sample
mints a second Mutation and splits the observations across both.
"""

import importlib

from django.apps import apps as django_apps
from django.test import TestCase

from aledb_common.util import GENE_LIST_LIMIT
from aledb_import.gene_annotation import get_annotated_gene_list
from aledb_seq.models import Mutation

migration = importlib.import_module("aledb_seq.migrations.0012_cap_recorded_gene_names")


def _range_of(count):
    names = ["gene%04d" % index for index in range(count)]
    return "%s–[%s]" % (names[0], names[-1]), ",".join(names)


class GeneCapMigrationTestCase(TestCase):

    def _mutation(self, gene, gene_name, position=1):
        return Mutation.objects.create(
            mutation_type="DEL", position=position, sequence_change="",
            gene=gene, annotation={"gene_name": gene_name} if gene_name else {})

    def _run(self):
        migration.cap_recorded_gene_names(django_apps, None)

    def test_an_over_limit_row_keeps_the_range_and_loses_the_names(self):
        gene_name, gene_product = _range_of(4318)
        mutation = self._mutation(gene_product, gene_name)

        self._run()

        mutation.refresh_from_db()
        self.assertEqual(gene_name, mutation.gene)
        self.assertNotIn("gene0500", mutation.gene)

    def test_what_it_writes_is_what_a_re_import_would_compute(self):
        """The whole point: the row has to still match, or the mutation forks."""
        gene_name, gene_product = _range_of(2343)
        mutation = self._mutation(gene_product, gene_name)

        self._run()

        mutation.refresh_from_db()
        self.assertEqual(", ".join(get_annotated_gene_list(gene_name, gene_product)),
                         mutation.gene)

    def test_a_row_at_or_under_the_limit_is_not_touched(self):
        gene_name, gene_product = _range_of(GENE_LIST_LIMIT)
        at_limit = self._mutation(gene_product, gene_name, position=1)
        ordinary = self._mutation("thrA, thrB", "thrA–thrB", position=2)

        self._run()

        at_limit.refresh_from_db()
        ordinary.refresh_from_db()
        self.assertEqual(gene_product, at_limit.gene)
        self.assertEqual("thrA, thrB", ordinary.gene)

    def test_a_row_with_no_gene_name_is_left_alone_rather_than_invented(self):
        _, gene_product = _range_of(2000)
        orphan = self._mutation(gene_product, None, position=3)

        self._run()

        orphan.refresh_from_db()
        self.assertEqual(gene_product, orphan.gene)

    def test_running_it_twice_changes_nothing_the_second_time(self):
        gene_name, gene_product = _range_of(1500)
        mutation = self._mutation(gene_product, gene_name)

        self._run()
        mutation.refresh_from_db()
        once = mutation.gene
        self._run()
        mutation.refresh_from_db()

        self.assertEqual(once, mutation.gene)

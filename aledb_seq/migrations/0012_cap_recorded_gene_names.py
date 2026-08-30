"""Stop recording a whole chromosome's gene names in `Mutation.gene`.

A structural variant spanning a gene range stored every name breseq listed -- 4,318 of them,
23,003 characters, in a `CharField(max_length=19000)` that SQLite does not enforce and Postgres
would have refused. `aledb_import.gene_annotation.get_annotated_gene_list` records the *range*
instead past `GENE_LIST_LIMIT`, and this moves the rows written before it did.

**It has to run, and not merely tidy up.** `gene` is one of the seven fields
`Mutation.objects.get_or_create` keys on, so a row left holding the long string would no longer
match what the importer now computes: re-importing that sample would mint a second `Mutation`
for the same biological mutation and split its observations across the two. The value written
here is exactly what the import path produces -- `annotation['gene_name']`, the range -- so the
next import finds the row it already has.

A row with no usable `gene_name` is left alone. There is nothing to fall back to, and inventing
a value would be a worse answer than the one it has.

Irreversible in substance: the names are not recoverable from what is kept, and re-annotating
(`./aledb reannotate`) is what puts a fuller answer back if one is ever wanted. The reverse is a
no-op rather than an error so the migration can still be unapplied.
"""

from django.db import migrations

from aledb_common.util import GENE_LIST_LIMIT, GENE_LIST_SPLIT


def cap_recorded_gene_names(apps, schema_editor):
    Mutation = apps.get_model("aledb_seq", "Mutation")

    capped = skipped = 0
    for mutation in (Mutation.objects
                     .exclude(gene__isnull=True).exclude(gene="")
                     .only("id", "gene", "annotation").iterator()):
        if len(GENE_LIST_SPLIT.split(mutation.gene)) <= GENE_LIST_LIMIT:
            continue
        range_name = (mutation.annotation or {}).get("gene_name")
        if not range_name:
            skipped += 1
            continue
        mutation.gene = range_name
        mutation.save(update_fields=["gene"])
        capped += 1

    if capped or skipped:
        print("\n  capped %d mutation gene lists over %d genes (%d left alone: no gene_name)"
              % (capped, GENE_LIST_LIMIT, skipped))


class Migration(migrations.Migration):

    dependencies = [("aledb_seq", "0011_drop_frequency_gatk")]

    operations = [
        migrations.RunPython(cap_recorded_gene_names, migrations.RunPython.noop),
    ]

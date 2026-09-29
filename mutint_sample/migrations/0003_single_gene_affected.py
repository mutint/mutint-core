"""Backfill `single_gene_affected` and `single_locus_tag_affected` into `Mutation.annotation`.

Both are derived from breseq's gene lists already in the blob, so no reference is read:
`mutint_import.annotation.annotation_values` computes them for every annotation written from
now on, and this computes them for the rows written before.

The rule is copied here rather than imported. A migration has to keep meaning what it meant
the day it ran, and `mutint_import.annotation` is free to change after that.

Elidable: a database created after this ran has no rows written without the keys, so a squash
may drop it.
"""

from django.db import migrations

FIELDS = (
    ("single_gene_affected", ("genes_inactivated", "genes_overlapping", "genes_promoter")),
    ("single_locus_tag_affected",
     ("locus_tags_inactivated", "locus_tags_overlapping", "locus_tags_promoter")),
)
BATCH = 2000


def _single_affected(annotation):
    values = {}
    for key, sources in FIELDS:
        union = set()
        for source in sources:
            value = annotation.get(source)
            if value:
                union.update(name for name in str(value).split(",") if name)
        if len(union) == 1:
            values[key] = union.pop()
    return values


def _rewrite(apps, compute):
    Mutation = apps.get_model("mutint_sample", "Mutation")
    batch = []
    rows = (Mutation.objects.exclude(annotation__isnull=True).exclude(annotation={})
            .only("id", "annotation").order_by("id").iterator(chunk_size=BATCH))
    for mutation in rows:
        annotation = mutation.annotation
        if not isinstance(annotation, dict):
            continue
        updated = {key: value for key, value in annotation.items()
                   if key not in dict(FIELDS)}
        updated.update(compute(updated))
        if updated != annotation:
            mutation.annotation = updated
            batch.append(mutation)
        if len(batch) >= BATCH:
            Mutation.objects.bulk_update(batch, ["annotation"])
            batch = []
    if batch:
        Mutation.objects.bulk_update(batch, ["annotation"])


def forwards(apps, schema_editor):
    _rewrite(apps, _single_affected)


def backwards(apps, schema_editor):
    _rewrite(apps, lambda annotation: {})


class Migration(migrations.Migration):

    dependencies = [
        ('mutint_sample', '0002_sample_treatment'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards, elidable=True),
    ]

"""`Sample.check_report_stored`: whether a curation check's report is in the store.

A second breseq report per sample, at `report-check/` beside the import's `report/`, written
by mutint-breseq when it re-runs breseq with the sample's curated mutations applied. A flag
rather than a directory check, for the reason `report_stored` is one: the storage kinds
measure from rows.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutint_sample", "0003_single_gene_affected"),
    ]

    operations = [
        migrations.AddField(
            model_name="sample",
            name="check_report_stored",
            field=models.BooleanField(default=False),
        ),
    ]

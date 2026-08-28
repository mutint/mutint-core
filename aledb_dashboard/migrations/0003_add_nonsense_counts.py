"""A column for `nonsense`, which the vocabulary was missing.

`FUNCTIONAL_CHANGE_TYPE_LIST` held five of breseq's six `snp_type` values, and `nonsense` -- a
substitution that introduces a stop codon, the most severe of them -- was not one. It has been
written by `aledb_import.annotate.annotator` since the port landed (`annotator.py:470`), and 392
SNPs in the dev database carry it, so they were counted as whatever else matched.

Both count models get the column, because the dashboard reports each bucket twice: once over
observations and once over distinct mutations.

`aledb_common.0004` depends on this migration, and the ordering is load-bearing -- see its
docstring.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aledb_dashboard', '0002_delete_barcharts'),
    ]

    operations = [
        migrations.AddField(
            model_name='observedmutationcounts',
            name='nonsense',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='uniquemutationcounts',
            name='nonsense',
            field=models.IntegerField(default=0),
        ),
    ]

"""Drop `breseq_present` and `gatk_present`, after moving what they meant into `present`.

They were one flag per caller, and every read path that asked "is this mutation in this
sample" asked *them* rather than `present` -- so a mutation added by a person, which no
caller found, was missing from the cross-sample table, the genome browser's sample menu and
the interop query. `present` carries whether it is there and `source` carries who said so,
which is the whole of what the pair was doing.

The backfill has to run before the drop: a row with `present` null and a caller flag set
renders today, and would silently stop once the flags are gone.
"""

from django.db import migrations, models


def carry_callers_into_present(apps, schema_editor):
    ObservedMutation = apps.get_model("aledb_seq", "ObservedMutation")
    ObservedMutation.objects.filter(present__isnull=True).filter(
        models.Q(breseq_present=True) | models.Q(gatk_present=True)
    ).update(present=True)


def unreachable(apps, schema_editor):
    """Deliberately a no-op rather than an error.

    Reversing cannot restore *which* caller found each mutation -- that is what dropping the
    columns spends. Re-adding them empty is what `RemoveField`'s own reverse does, and
    refusing to reverse at all would make the whole migration irreversible for the sake of
    information that is already gone.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_seq", "0009_backfill_reference_identity"),
    ]

    operations = [
        migrations.RunPython(carry_callers_into_present, unreachable),
        migrations.RemoveField(model_name="observedmutation", name="breseq_present"),
        migrations.RemoveField(model_name="observedmutation", name="gatk_present"),
    ]

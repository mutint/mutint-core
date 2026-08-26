"""Collapse duplicate experiment filters, then make a second one impossible.

`AleExperimentFilter.ale_experiment` was a plain ForeignKey, so nothing stopped an experiment
having several filter rows -- and `ensure_default_experiment_filter` created one every time it
ran against an experiment whose filter had been edited, because it passed the factory defaults
as `get_or_create` *lookups* rather than as `defaults`. Two rows made
`filter_observed_mutations` OR both of their exclusions together, silently changing what every
mutation table showed, and made `ale_exp_filter`'s `.get(ale_experiment_id=...)` raise
MultipleObjectsReturned.

The repair and the constraint are one migration on purpose. The AlterField cannot apply while
duplicates exist, and the repair on its own would leave the door open for more -- there is no
state between the two worth being able to stop at.
"""

from django.db import migrations, models
import django.db.models.deletion


def dedupe(apps, schema_editor):
    """Keep the lowest pk per experiment and delete the rest.

    Lowest, not highest, because of how the duplicates arose: the original row is created
    first and then *edited*, and the bug appends a fresh row carrying the factory defaults.
    The earliest row is therefore the one holding whatever the user actually chose, and the
    later ones are the settings they had already overridden.
    """
    AleExperimentFilter = apps.get_model("aledb_filter", "AleExperimentFilter")

    seen = set()
    superseded = []
    for pk, experiment_id in (AleExperimentFilter.objects
                              .order_by("pk")
                              .values_list("pk", "ale_experiment_id")):
        if experiment_id in seen:
            superseded.append(pk)
        else:
            seen.add(experiment_id)

    if superseded:
        AleExperimentFilter.objects.filter(pk__in=superseded).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('aledb_experiment', '0003_backfill_view_project_grants'),
        ('aledb_filter', '0001_initial'),
    ]

    operations = [
        # noop reverse: the deleted rows were duplicates of settings nobody chose, and
        # inventing rows to restore them would be worse than the constraint being dropped.
        migrations.RunPython(dedupe, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='aleexperimentfilter',
            name='ale_experiment',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                       to='aledb_experiment.aleexperiment'),
        ),
    ]

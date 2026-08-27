"""Drop the three mutation-id hide lists.

Depends on `aledb_mutation_editor.0002`, which reads these columns, so the ordering is not
incidental: reversing it would drop the data before anything had moved it.

The columns come back empty on a reverse migration, which is as much as can honestly be
offered -- what was in them lives in the change log now, in a form that says who and when.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_filter", "0002_one_filter_per_experiment"),
        # Not merely an ordering hint: 0002 is what reads these columns before they go.
        ("aledb_mutation_editor", "0002_import_filter_hidden_mutations"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="aleexperimentfilter",
            name="ignored_mutations",
        ),
        migrations.RemoveField(
            model_name="aleexperimentfilter",
            name="starting_strain_mutations",
        ),
        migrations.RemoveField(
            model_name="globalfilter",
            name="ignored_mutations",
        ),
    ]

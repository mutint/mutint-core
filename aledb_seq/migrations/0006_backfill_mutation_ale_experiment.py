"""Point existing mutations at the experiment they belong to.

0005 added Mutation.ale_experiment as a nullable FK and populated it only for
mutations imported afterwards, so every row that predates it kept NULL. Nothing
failed loudly -- but `./aledb reannotate` filters on that FK, so it reported
"has no mutations" for every experiment with legacy data, and those mutations
could never gain the annotation the Samples page renders from.

The experiment is recoverable from the observation chain, which is how
ObservedMutation.get_experiment_id() has always found it:

    ObservedMutation -> sequencing_experiment -> tech_rep -> isolate -> flask
                     -> ale_id -> ale_experiment

A mutation observed in samples from more than one experiment is deliberately left
alone. Mutations are meant to be per-experiment -- two experiments calling the
same variant get their own rows, so re-annotating one cannot rewrite another's --
and picking one of several here would quietly attach it to the wrong one. Leaving
it NULL keeps today's behaviour for that row rather than inventing an answer.
"""

from django.db import migrations

from aledb_seq.migrations._backfill_rule import resolve


def backfill(apps, schema_editor):
    Mutation = apps.get_model("aledb_seq", "Mutation")
    ObservedMutation = apps.get_model("aledb_seq", "ObservedMutation")

    unlinked = set(
        Mutation.objects.filter(ale_experiment__isnull=True).values_list("id", flat=True))
    if not unlinked:
        return

    resolved = [Mutation(id=mutation_id, ale_experiment_id=experiment_id)
                for mutation_id, experiment_id
                in resolve(ObservedMutation, unlinked).items()]

    for start in range(0, len(resolved), 1000):
        Mutation.objects.bulk_update(
            resolved[start:start + 1000], ["ale_experiment"])


def unbackfill(apps, schema_editor):
    """Reversible as a no-op.

    Clearing the column would also clear rows set at import, which this never
    touched, and there is no record of which were which.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_seq", "0005_mutation_ale_experiment_mutation_annotation_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]

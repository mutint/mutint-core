"""Add the designated ancestor, and carry the ALE-"0" convention forward into it.

**Convert, then drop** -- the posture `aledb_filter.0005` took with the global filter's genes
and `aledb_mutation_editor.0002` took with the hidden mutation lists. What is being dropped
here is `STARTING_STRAIN_ALE_ID = "0"`: the convention that an ALE labelled `0` held the
starting strain, which four pages used to keep that ALE out of their picker and which the
dashboard used to keep it out of three counts.

Nothing depended on that label after this migration, so **without a backfill every existing
A0 starting strain would silently reappear** -- in the ALE menus, in the dashboard totals, and
in every plugin table -- with nothing designating it as anything. So each experiment that has
exactly one sample under ALE `0` gets that sample as its `ancestor`, which hides it again and,
for the first time, subtracts its mutations from the other samples.

**Ambiguity is skipped, not guessed.** An experiment with several samples under ALE `0` gets
no designation and a warning: "which of these three is the ancestor" is a question about the
data that only whoever ran the experiment can answer, and choosing one for them would be a
silent, wrong answer rather than a visible, absent one. Those experiments show every A0 sample
until someone designates one at `/ale/experiment/<pk>/ancestor/`.

`ancestor_set_by` is left null, which the page renders as the system having set it -- the same
thing `aledb_mutation_editor.0002` does with `created_by`.

Also drops `AleId.starting_strain`, a FK to `Isolate` that no code path in the suite ever
wrote. It was a third spelling of this idea, and it never held a value.

The backfill is reversible in the sense that matters -- the columns go away on the way back --
but which sample was designated is not reconstructed, because after the fact it is
indistinguishable from one designated by hand.
"""

import logging

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

logger = logging.getLogger(__name__)

#: The label this migration reads once and nothing reads afterwards.
_STARTING_STRAIN_ALE_ID = "0"


def designate_a0_starting_strains(apps, schema_editor):
    AleExperiment = apps.get_model("aledb_experiment", "AleExperiment")
    ResequencingExperiment = apps.get_model("aledb_seq", "ResequencingExperiment")

    designated = skipped = 0
    for experiment in AleExperiment.objects.all():
        samples = list(ResequencingExperiment.objects.filter(
            tech_rep__isolate__flask__ale_id__ale_experiment=experiment,
            tech_rep__isolate__flask__ale_id__ale_id=_STARTING_STRAIN_ALE_ID)[:2])
        if not samples:
            continue
        if len(samples) > 1:
            skipped += 1
            logger.warning(
                "experiment %s has more than one sample under ALE %s; designating none. "
                "Pick one at /ale/experiment/%s/ancestor/.",
                experiment.pk, _STARTING_STRAIN_ALE_ID, experiment.pk)
            continue
        experiment.ancestor = samples[0]
        # No timestamp: this was not a decision anybody made on a date, it was a convention
        # being read. `ancestor_set_by` stays null and renders as "system".
        experiment.save(update_fields=["ancestor"])
        designated += 1

    if designated or skipped:
        logger.info("designated %d ancestor(s) from ALE %s, skipped %d ambiguous",
                    designated, _STARTING_STRAIN_ALE_ID, skipped)


def undesignate(apps, schema_editor):
    """Reverse leaves the designations in place; the columns are dropped after this."""


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("aledb_experiment", "0009_alter_project_user"),
        ("aledb_seq", "0012_cap_recorded_gene_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="aleexperiment",
            name="ancestor",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="ancestor_of",
                                    to="aledb_seq.resequencingexperiment"),
        ),
        migrations.AddField(
            model_name="aleexperiment",
            name="ancestor_set_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aleexperiment",
            name="ancestor_set_by",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(designate_a0_starting_strains, undesignate),
        migrations.RemoveField(model_name="aleid", name="starting_strain"),
    ]

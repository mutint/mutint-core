"""Drop the per-row breseq path columns and the HTML-scraped evidence columns.

`location`, `experiment_location` and `gatk_location` existed only to build breseq HTML
report URLs. They were also `get_or_create` *lookup* kwargs in the CLI importer, which meant
a sample forked a duplicate row whenever one of those paths changed -- when the HTML report
appeared, or ALE_DATA_ROOT_DIR moved. The narrowed key is (sample_name, tech_rep, person),
which is what the web importer already used.

Any database that accumulated those forks would raise MultipleObjectsReturned on its first
import after this migration, so the duplicates are merged first.
"""

from django.db import migrations
from django.db.models import Count, Min


def merge_forked_samples(apps, schema_editor):
    """Collapse rows that differ only in the path columns being dropped."""
    ResequencingExperiment = apps.get_model("aledb_seq", "ResequencingExperiment")
    ObservedMutation = apps.get_model("aledb_seq", "ObservedMutation")
    MissingCoverage = apps.get_model("aledb_seq", "UnassignedMissingCoverageEvidence")

    duplicated = (ResequencingExperiment.objects
                  .values("sample_name", "tech_rep_id", "person")
                  .annotate(keep=Min("id"), n=Count("id"))
                  .filter(n__gt=1))

    for group in duplicated:
        keep = group["keep"]
        losers = list(ResequencingExperiment.objects
                      .filter(sample_name=group["sample_name"],
                              tech_rep_id=group["tech_rep_id"],
                              person=group["person"])
                      .exclude(id=keep)
                      .values_list("id", flat=True))
        if not losers:
            continue
        ObservedMutation.objects.filter(
            sequencing_experiment_id__in=losers).update(sequencing_experiment_id=keep)
        MissingCoverage.objects.filter(
            sequencing_experiment_id__in=losers).update(sequencing_experiment_id=keep)
        ResequencingExperiment.objects.filter(id__in=losers).delete()


def unmerge(apps, schema_editor):
    """Irreversible: the rows the merge deleted differed only in dropped columns."""


class Migration(migrations.Migration):

    dependencies = [
        ('aledb_seq', '0003_resequencingexperiment_bam_stored_and_more'),
    ]

    operations = [
        migrations.RunPython(merge_forked_samples, unmerge),
        migrations.RemoveField(
            model_name='observedmutation',
            name='evidence',
        ),
        migrations.RemoveField(
            model_name='observedmutation',
            name='gatk_evidence',
        ),
        migrations.RemoveField(
            model_name='resequencingexperiment',
            name='experiment_location',
        ),
        migrations.RemoveField(
            model_name='resequencingexperiment',
            name='gatk_location',
        ),
        migrations.RemoveField(
            model_name='resequencingexperiment',
            name='location',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='coverage',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='description',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='gene',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='reads_left',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='reads_left_url',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='reads_right',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='reads_right_url',
        ),
        migrations.RemoveField(
            model_name='unassignedmissingcoverageevidence',
            name='size',
        ),
    ]

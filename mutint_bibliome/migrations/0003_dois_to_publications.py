"""Every DOI held in `Experiment.doi` becomes a `Publication` row before that column goes.

`Experiment.doi` was a space-separated string beside this app's table, and the two never
agreed. This runs against the historical model, so it reads the column that
`mutint_experiment.0003` removes; that migration depends on this one.
"""

from django.db import migrations

DOI_URL = "https://doi.org/"


def dois_to_publications(apps, schema_editor):
    Experiment = apps.get_model("mutint_experiment", "Experiment")
    Publication = apps.get_model("mutint_bibliome", "Publication")
    for experiment in Experiment.objects.exclude(doi__isnull=True).exclude(doi=""):
        present = set(Publication.objects.filter(experiment=experiment)
                      .values_list("url", flat=True))
        for doi in experiment.doi.split():
            url = DOI_URL + doi
            if url not in present:
                Publication.objects.create(experiment=experiment, title=doi, url=url)
                present.add(url)


class Migration(migrations.Migration):

    dependencies = [
        ("mutint_bibliome", "0002_initial"),
        ("mutint_experiment", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(dois_to_publications, migrations.RunPython.noop),
    ]

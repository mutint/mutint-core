from django.db import models

from aledb_experiment.models import AleExperiment


class Publication(models.Model):
    url = models.URLField()
    title = models.TextField()
    ale_experiment = models.ForeignKey(AleExperiment, on_delete=models.DO_NOTHING)

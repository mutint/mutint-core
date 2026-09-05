from django.db import models

from mutint_experiment.models import Experiment


class Publication(models.Model):
    url = models.URLField()
    title = models.TextField()
    experiment = models.ForeignKey(Experiment, on_delete=models.DO_NOTHING)

from django.db import models

from mutint_experiment.models import Experiment


class Publication(models.Model):
    """A paper an experiment is described in. See `mutint_bibliome.publication`."""

    url = models.URLField()
    title = models.TextField()
    experiment = models.ForeignKey(Experiment, on_delete=models.DO_NOTHING)

    DOI_URL = "https://doi.org/"

    @property
    def doi(self):
        """The bare DOI when the URL is a doi.org link, else None."""
        if self.url.startswith(self.DOI_URL):
            return self.url[len(self.DOI_URL):]
        return None

    def __str__(self):
        return self.title

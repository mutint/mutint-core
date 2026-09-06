"""The publications an experiment cites, and the DOI shorthand people type them as.

`Publication` is the one record: a title and a URL per paper, one row per experiment it
describes. `Experiment.doi` used to hold a space-separated string beside it, edited on the
experiment form and rendered as links, and the two never agreed -- the form wrote one, the
Overview read the other. The form still takes DOIs, because that is what people have to
hand; it lands here as `Publication` rows whose URL is `https://doi.org/<doi>` and whose
title is the DOI itself until somebody gives it a better one.
"""

from mutint_bibliome.models import Publication
from mutint_experiment.models import Experiment

DOI_URL = "https://doi.org/"


def create_publication(title_str, url_str, experiment_pk):
    exp = Experiment.objects.get(pk=experiment_pk)
    return Publication.objects.create(title=title_str, url=url_str, experiment=exp)


def publications_for(experiment):
    """An experiment's publications, oldest first."""
    return Publication.objects.filter(experiment=experiment).order_by("id")


def dois_for(experiment):
    """The DOIs among an experiment's publications, in order, as bare strings."""
    return [pub.doi for pub in publications_for(experiment) if pub.doi]


def set_dois(experiment, dois, replace=True):
    """Make `dois` (a space-separated string or a list) the experiment's DOI publications.

    With `replace`, every publication that is a DOI link is dropped first; publications with
    any other URL are left alone, since nothing on the form can express them. Without it the
    new DOIs are appended, skipping any already present.
    """
    if isinstance(dois, str):
        dois = dois.split()
    existing = publications_for(experiment)
    if replace:
        existing.filter(url__startswith=DOI_URL).delete()
        present = set()
    else:
        present = {pub.doi for pub in existing if pub.doi}
    for doi in dois:
        if doi in present:
            continue
        Publication.objects.create(title=doi, url=DOI_URL + doi, experiment=experiment)
        present.add(doi)


def add_publication_to_experiment(experiment_ids, doi, replace=False):
    for experiment_id in experiment_ids:
        try:
            experiment = Experiment.objects.get(pk=experiment_id)
        except Experiment.DoesNotExist:
            continue
        set_dois(experiment, doi, replace=replace)

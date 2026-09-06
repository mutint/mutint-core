from mutint_bibliome.models import Publication
from mutint_experiment.models import Experiment


def create_publication(title_str, url_str, experiment_pk):
    exp = Experiment.objects.get(pk=experiment_pk)
    pub = Publication.objects.create(title=title_str, url=url_str, experiment=exp)


def add_publication_to_experiment(experiment_ids, doi, replace=False):
    for id in experiment_ids:

        try:
            curr_experiment = Experiment.objects.get(pk=id)
            curr_doi = curr_experiment.doi
            if replace:
                curr_experiment.doi = doi
            elif len(curr_doi) > 0:
                curr_experiment.doi = curr_experiment.doi + " " + doi
            else:
                curr_experiment.doi = doi
            curr_experiment.save()

            for bib in curr_experiment.doi_as_list():
                create_publication(bib, "https://doi.org/" + bib, id)
        except Experiment.DoesNotExist:
            continue


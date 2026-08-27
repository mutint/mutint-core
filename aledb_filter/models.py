from django.db import models

from aledb_experiment.models import AleExperiment

from aledb_filter.common import DEFAULT_MUTATION_FREQ_MIN
from aledb_filter.common import DEFAULT_MUTATION_FREQ_MAX


def get_default_experiment_filter_params(ale_experiment):
    default_experiment_filter_params = {
            'ale_experiment': ale_experiment,
            'min_cutoff': DEFAULT_MUTATION_FREQ_MIN,
            'max_cutoff': DEFAULT_MUTATION_FREQ_MAX,
            'min_gatk_cutoff': DEFAULT_MUTATION_FREQ_MIN,
            'max_gatk_cutoff': DEFAULT_MUTATION_FREQ_MAX,
            'ignored_genes': ""}
    return default_experiment_filter_params


class AleExperimentFilter(models.Model):
    """One experiment's mutation filter settings. Exactly one row per experiment.

    A OneToOneField rather than a plain ForeignKey, because every piece of code that reads
    this has always assumed one row -- `ale_exp_filter` calls `.get(ale_experiment_id=...)`
    and the filter form edits a single instance -- while nothing in the database said so.
    Two rows made `filter_observed_mutations` OR both of their exclusions together, which
    quietly changed what every mutation table showed, and made that `.get()` raise.
    """

    ale_experiment = models.OneToOneField(AleExperiment, on_delete=models.CASCADE)
    min_cutoff = models.PositiveSmallIntegerField(default=DEFAULT_MUTATION_FREQ_MIN)  # TODO: this should like rather be a decimal to it's conterpart of aledb_seq.models.ObservedMutation.frequency
    max_cutoff = models.PositiveSmallIntegerField(default=DEFAULT_MUTATION_FREQ_MAX)  # TODO: this should like rather be a decimal to it's conterpart of aledb_seq.models.ObservedMutation.frequency
    min_gatk_cutoff = models.PositiveSmallIntegerField(
        default=DEFAULT_MUTATION_FREQ_MIN)  # TODO: this should like rather be a decimal to it's conterpart of aledb_seq.models.ObservedMutation.frequency
    max_gatk_cutoff = models.PositiveSmallIntegerField(
        default=DEFAULT_MUTATION_FREQ_MAX)  # TODO: this should like rather be a decimal to it's conterpart of aledb_seq.models.ObservedMutation.frequency

    ignored_genes = models.TextField(default='', blank=True)

    # `ignored_mutations` and `starting_strain_mutations` used to sit here: comma-joined
    # Mutation ids, with no foreign key and nothing that ever pruned them, excluded from every
    # table by `filter_observed_mutations`. They were a way of deleting a mutation while
    # keeping the row -- per experiment rather than per sample, unattributed, and impossible to
    # undo. `aledb_mutation_editor` replaces them; migration 0003 moved what was in them into
    # its change log, so what was hidden is still hidden and is now also visible and
    # restorable.


class GlobalFilter(models.Model):

    ignored_genes = models.CharField(max_length=15000, default='', blank=True)

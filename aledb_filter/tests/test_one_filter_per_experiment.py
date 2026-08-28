"""An experiment has exactly one filter row, and nothing can quietly give it two.

`ensure_default_experiment_filter` used to call `get_or_create` with the factory defaults as
*lookups* -- "a filter for this experiment whose settings are all still the defaults" -- so
once anyone edited a filter it no longer matched their row and created a second one beside it.
Nothing in the database objected, because `ale_experiment` was a plain ForeignKey.

That was live and silent rather than loud. Two rows make `filter_observed_mutations` OR both
of their exclusions together, so an experiment's mutation tables start showing a different set
of rows with no error anywhere; the `.get(ale_experiment_id=...)` in `ale_exp_filter` only
starts raising afterwards. The registry added on 2026-08-26 made the rebuild run far more
often than the old import-only path did, which is what turned a latent bug into a reachable
one.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from aledb_experiment.models import AleExperiment, Instrument
from aledb_filter.models import AleExperimentFilter
from aledb_filter.util import (
    ensure_default_experiment_filter, filtered_observed_mutation_queryset,
)
from aledb_seq.models import ObservedMutation


class OneFilterPerExperimentTestCase(TestCase):

    def setUp(self):
        self.experiment = AleExperiment.objects.create(
            instrument=Instrument.objects.create())

    def _filters(self):
        return AleExperimentFilter.objects.filter(ale_experiment=self.experiment)

    def test_it_creates_a_filter_when_there_is_none(self):
        ensure_default_experiment_filter(self.experiment.ale_id)
        self.assertEqual(1, self._filters().count())

    def test_running_it_twice_creates_one_filter(self):
        ensure_default_experiment_filter(self.experiment.ale_id)
        ensure_default_experiment_filter(self.experiment.ale_id)
        self.assertEqual(1, self._filters().count())

    def test_it_does_not_duplicate_an_edited_filter(self):
        """The regression. An edited filter no longer matched the all-defaults lookup."""
        ensure_default_experiment_filter(self.experiment.ale_id)
        self._filters().update(min_cutoff=55, ignored_genes="thrA")

        ensure_default_experiment_filter(self.experiment.ale_id)

        self.assertEqual(1, self._filters().count(),
                         "a second filter row was created beside the edited one")

    def test_it_leaves_an_edited_filter_untouched(self):
        """`defaults=` must not be written over settings that already exist."""
        ensure_default_experiment_filter(self.experiment.ale_id)
        self._filters().update(min_cutoff=55, ignored_genes="thrA")

        ensure_default_experiment_filter(self.experiment.ale_id)

        edited = self._filters().get()
        self.assertEqual(55, edited.min_cutoff)
        self.assertEqual("thrA", edited.ignored_genes)

    def test_it_does_nothing_for_an_experiment_that_does_not_exist(self):
        ensure_default_experiment_filter(999999)
        self.assertEqual(0, AleExperimentFilter.objects.count())

    def test_the_database_refuses_a_second_filter(self):
        """The constraint, not just the call site.

        The lookup fix stops this one path from creating duplicates; the OneToOneField is
        what stops the next path from doing it again. Three places create these rows.
        """
        AleExperimentFilter.objects.create(ale_experiment=self.experiment)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AleExperimentFilter.objects.create(ale_experiment=self.experiment)

    def test_two_experiments_may_each_have_one(self):
        """The constraint is per experiment, not global."""
        other = AleExperiment.objects.create(instrument=Instrument.objects.create())

        ensure_default_experiment_filter(self.experiment.ale_id)
        ensure_default_experiment_filter(other.ale_id)

        self.assertEqual(2, AleExperimentFilter.objects.count())

    def test_the_filter_queryset_reads_one_row(self):
        """Why it mattered: duplicates were ORed together, changing what tables showed.

        `filtered_observed_mutation_queryset` iterates every filter row matching the
        experiment and adds each one's exclusions with Q.OR, so a stray defaults row widened
        the exclusion beyond what the user had asked for.
        """
        ensure_default_experiment_filter(self.experiment.ale_id)
        self._filters().update(min_cutoff=55)
        ensure_default_experiment_filter(self.experiment.ale_id)

        _queryset, _genes_by_experiment = filtered_observed_mutation_queryset(
            ObservedMutation.objects.all(), self.experiment.ale_id)

        self.assertEqual(1, self._filters().count())

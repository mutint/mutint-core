"""The migration that brought the experiment filter's hidden mutations into the change log.

`forwards` is called directly rather than through a migration-replay harness -- there is none
in this repo, and `aledb_experiment.tests.test_access_migration` set the precedent.

What is stubbed, and why it has to be: `AleExperimentFilter.ignored_mutations`,
`.starting_strain_mutations` and `GlobalFilter.ignored_mutations` **no longer exist**, on the
model or in the database, because `aledb_filter.0003` drops them immediately after this
migration reads them. So the two filter models are the one thing a real registry cannot supply
here. Everything else -- the mutations, the observations, the changesets it writes and the rows
it deletes -- is the genuine article, which is where all the behaviour worth pinning lives.

This will run exactly once on a real database, which is the reason to test it at all.
"""

import importlib

from django.apps import apps as django_apps
from django.db import connection

from aledb_common.models import DerivedDataState
from aledb_mutation_editor import history
from aledb_mutation_editor.models import MutationChange, MutationChangeSet
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import ObservedMutation

migration = importlib.import_module(
    "aledb_mutation_editor.migrations.0002_import_filter_hidden_mutations")


class FakeSchemaEditor:
    connection = connection


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Model:
    def __init__(self, rows):
        self.objects = _Rows(rows)


class _FilterRow:
    """Stands in for a filter row as it was before 0003 dropped these three columns."""

    def __init__(self, ignored_mutations="", starting_strain_mutations=""):
        self.ignored_mutations = ignored_mutations
        self.starting_strain_mutations = starting_strain_mutations


class _FakeApps:
    """The real registry, with the two filter models replaced by the stubs above."""

    def __init__(self, experiment_filters=(), global_filters=()):
        self._replacements = {
            ("aledb_filter", "AleExperimentFilter"): _Model(experiment_filters),
            ("aledb_filter", "GlobalFilter"): _Model(global_filters),
        }

    def get_model(self, app_label, model_name):
        key = (app_label, model_name)
        if key in self._replacements:
            return self._replacements[key]
        return django_apps.get_model(app_label, model_name)


class IdParsingTestCase(EditorTestCase):
    """`_ids` repeats what `get_ignored_mut_id_list_from_str` did, including its tolerance."""

    def test_it_reads_a_comma_joined_list(self):
        self.assertEqual([1, 2, 3], migration._ids("1,2,3"))

    def test_it_tolerates_whitespace_and_empty_entries(self):
        """Real lists ended with a trailing comma: the writer was a bare string concat."""
        self.assertEqual([1, 2], migration._ids(" 1, ,2,"))

    def test_it_drops_anything_that_is_not_an_id(self):
        self.assertEqual([7], migration._ids("7,not-an-id"))

    def test_an_empty_column_is_an_empty_list(self):
        for empty in ("", None):
            with self.subTest(value=empty):
                self.assertEqual([], migration._ids(empty))


class MigrationTestCase(EditorTestCase):

    def run_forwards(self, ignored_mutations="", starting_strain_mutations="",
                     global_ignored=""):
        fake = _FakeApps(
            experiment_filters=[_FilterRow(ignored_mutations, starting_strain_mutations)],
            global_filters=[_FilterRow(global_ignored)])
        migration.forwards(fake, FakeSchemaEditor())

    # --- what it does ---------------------------------------------------------------------

    def test_a_hidden_mutation_loses_its_observations(self):
        self.run_forwards(ignored_mutations=str(self.mut_1.id))

        self.assertFalse(ObservedMutation.objects.filter(mutation=self.mut_1).exists())
        # mut_1 was observed in both samples; hiding was per experiment, so both go.
        self.assertEqual({self.mut_2.id, self.mut_3.id}, self.observed_ids(self.sample_a))
        self.assertEqual(set(), self.observed_ids(self.sample_b))

    def test_it_leaves_the_mutation_row_alone(self):
        self.run_forwards(ignored_mutations=str(self.mut_1.id))
        self.mut_1.refresh_from_db()
        self.assertEqual(100, self.mut_1.position)

    def test_it_writes_one_changeset_per_experiment_not_per_mutation(self):
        self.run_forwards(
            ignored_mutations="%d,%d" % (self.mut_1.id, self.mut_2.id))

        self.assertEqual(1, MutationChangeSet.objects.count())
        # mut_1 in two samples plus mut_2 in one.
        self.assertEqual(3, MutationChange.objects.count())

    def test_the_changeset_is_attributed_to_the_system(self):
        self.run_forwards(ignored_mutations=str(self.mut_1.id))

        change_set = MutationChangeSet.objects.get()
        self.assertIsNone(change_set.created_by_id)
        self.assertTrue(change_set.is_system)
        self.assertEqual("delete", change_set.kind)
        self.assertIsNotNone(change_set.created_at)

    def test_the_note_names_which_list_it_came_from(self):
        for column, expected in (("ignored_mutations", "ignored mutations"),
                                 ("starting_strain_mutations", "starting strain mutations")):
            with self.subTest(column=column):
                MutationChangeSet.objects.all().delete()
                self.run_forwards(**{column: str(self.mut_2.id)})
                self.assertIn(expected, MutationChangeSet.objects.get().note)
                history.restore(self.experiment, self.owner, None)

    def test_the_global_list_is_migrated_too(self):
        """Its ids are still per-experiment Mutation ids, so they land in that experiment."""
        self.run_forwards(global_ignored=str(self.mut_3.id))

        self.assertFalse(ObservedMutation.objects.filter(mutation=self.mut_3).exists())
        self.assertIn("global", MutationChangeSet.objects.get().note)

    # --- what it makes possible -----------------------------------------------------------

    def test_what_was_hidden_can_now_be_restored(self):
        """The whole point of migrating rather than dropping: it becomes undoable."""
        before = {
            observed.sequencing_experiment_id: history.observation_snapshot(observed)
            for observed in ObservedMutation.objects.filter(mutation=self.mut_1)}

        self.run_forwards(ignored_mutations=str(self.mut_1.id))
        history.restore(self.experiment, self.owner, None)

        after = {
            observed.sequencing_experiment_id: history.observation_snapshot(observed)
            for observed in ObservedMutation.objects.filter(mutation=self.mut_1)}
        self.assertEqual(before, after)

    def test_it_marks_the_derived_data_stale(self):
        """The stored counts were computed under the old filter, so they must be recomputed
        even though they should come out identical."""
        DerivedDataState.objects.update(stale_since=None)

        self.run_forwards(ignored_mutations=str(self.mut_1.id))

        self.assertFalse(
            DerivedDataState.objects.filter(stale_since__isnull=True).exists())

    # --- what it declines to do -----------------------------------------------------------

    def test_an_empty_filter_writes_nothing(self):
        self.run_forwards()

        self.assertEqual(0, MutationChangeSet.objects.count())
        self.assertEqual(4, self.observation_count())

    def test_an_id_naming_nothing_observed_writes_nothing(self):
        """Nothing ever pruned these lists, so real ones carry ids that are long gone."""
        self.run_forwards(ignored_mutations="999999")

        self.assertEqual(0, MutationChangeSet.objects.count())
        self.assertEqual(4, self.observation_count())

    def test_it_does_not_mark_anything_stale_when_there_was_nothing_to_do(self):
        DerivedDataState.objects.update(stale_since=None)

        self.run_forwards()

        self.assertFalse(
            DerivedDataState.objects.filter(stale_since__isnull=False).exists())

"""`aledb_seq.0010` has to move what the caller flags meant into `present` before dropping them.

Run through the real migration executor rather than by calling the function with a fake apps
registry, which is how `aledb_mutation_editor.tests.test_filter_migration` tests its own data
migration. That shortcut is not available here: the columns this one reads no longer exist on
the live model, so the only way to have them is to stand the database up at 0009.

What could go wrong is the *order*. A `RemoveField` that ran before the `RunPython` would
leave every row whose presence was recorded only in a caller flag looking like a row about
which nothing was ever recorded -- and those rows render nowhere, so the loss would be
invisible rather than loud.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

APP = "aledb_seq"
BEFORE = "0009_backfill_reference_identity"
AFTER = "0010_drop_caller_present_flags"


class CallerFlagBackfillTestCase(TransactionTestCase):
    # The rows are inserted with raw SQL against the 0009 schema, so no fixtures are loaded
    # and nothing here depends on another app's tables.
    available_apps = None

    def setUp(self):
        # Created before the schema moves: `ObservedMutation.mutation_id` is NOT NULL, and
        # `Mutation` is untouched by this migration, so the row is still there at 0009 and
        # still there again afterwards.
        from aledb_seq.models import Mutation
        self.mutation = Mutation.objects.create(
            mutation_type="SNP", position=1, sequence_change="A>T")

        self.addCleanup(self._migrate, AFTER)
        self._migrate(BEFORE)

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(APP, target)])

    def _insert(self, present, breseq_present, gatk_present):
        """One ObservedMutation, in raw SQL because the ORM model has no caller flags.

        `sequencing_experiment_id` is left null: this migration reads neither foreign key,
        and standing up a real experiment chain would be several tables of setup for a test
        about three boolean columns.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO aledb_seq_observedmutation "
                "(mutation_id, present, breseq_present, gatk_present) VALUES (%s, %s, %s, %s)",
                [self.mutation.pk, present, breseq_present, gatk_present])
            return cursor.lastrowid

    def _present(self, row_id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT present FROM aledb_seq_observedmutation WHERE id = %s",
                           [row_id])
            return cursor.fetchone()[0]

    def test_a_row_present_only_by_its_caller_flag_keeps_rendering(self):
        """The rows the backfill exists for: imported before `present` was written."""
        breseq = self._insert(None, True, None)
        gatk = self._insert(None, None, True)

        self._migrate(AFTER)

        self.assertEqual(1, self._present(breseq))
        self.assertEqual(1, self._present(gatk))

    def test_a_row_recorded_absent_stays_absent(self):
        """`present=False` means looked for and not found. Widening the backfill to `update
        every row` would turn every one of those into an assertion that it is there."""
        absent = self._insert(False, False, False)

        self._migrate(AFTER)

        self.assertEqual(0, self._present(absent))

    def test_a_row_with_nothing_recorded_stays_that_way(self):
        """It rendered nowhere before and renders nowhere after. Guessing True here would
        invent observations that no caller and no person ever asserted."""
        unknown = self._insert(None, None, None)

        self._migrate(AFTER)

        self.assertIsNone(self._present(unknown))

    def test_the_columns_are_gone_afterwards(self):
        self._migrate(AFTER)

        columns = {column.name for column in
                   connection.introspection.get_table_description(
                       connection.cursor(), "aledb_seq_observedmutation")}
        self.assertNotIn("breseq_present", columns)
        self.assertNotIn("gatk_present", columns)
        self.assertIn("present", columns)

"""The A0 backfill in `aledb_experiment.0010`.

Retiring `STARTING_STRAIN_ALE_ID` is what makes this necessary rather than nice. Nothing reads
the label `"0"` after that migration, so **without the backfill every existing starting strain
silently reappears** -- in the ALE menus, in the dashboard totals and in every plugin table --
with nothing designating it as anything. "Convert, then drop", the posture `aledb_filter.0005`
and `aledb_mutation_editor.0002` both take.

No development database has a sample under ALE `0`, so nothing but this file exercises it. That
is the reason it is tested directly rather than left to be noticed on somebody's data.

The migration function is called with a stub `apps` returning the live models: it only ever
calls `get_model`, and the three columns it touches have not changed since.
"""

from importlib import import_module

from django.apps import apps as live_apps
from django.test import TestCase

from aledb_experiment.models import (AleExperiment, AleId, Flask, FreezerBox, Instrument,
                                     Isolate, Media, TechnicalReplicate)
from aledb_seq.models import ResequencingExperiment

#: Not a normal import: the module name starts with a digit.
migration = import_module("aledb_experiment.migrations.0010_designated_ancestor")


class BackfillTestCase(TestCase):

    def setUp(self):
        self.media = Media.objects.create()
        self.freezer_box = FreezerBox.objects.create()
        self.experiment = AleExperiment.objects.create(
            instrument=Instrument.objects.create())

    def make_sample(self, ale_label, isolate_number="1"):
        ale, _ = AleId.objects.get_or_create(ale_experiment=self.experiment,
                                             ale_id=ale_label)
        flask, _ = Flask.objects.get_or_create(ale_id=ale, flask_number=1,
                                               defaults={"media": self.media})
        isolate = Isolate.objects.create(flask=flask, isolate_number=isolate_number,
                                         is_population=False,
                                         freezer_box=self.freezer_box)
        rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        return ResequencingExperiment.objects.create(tech_rep=rep)

    def run_backfill(self):
        migration.designate_a0_starting_strains(live_apps, None)
        self.experiment.refresh_from_db()


class TestTheBackfill(BackfillTestCase):

    def test_a_single_a0_sample_becomes_the_ancestor(self):
        ancestor = self.make_sample("0")
        self.make_sample("1")
        self.run_backfill()
        self.assertEqual(self.experiment.ancestor_id, ancestor.id)

    def test_it_leaves_set_by_null_so_the_page_says_system(self):
        """Nobody made this decision on a date; a convention was read."""
        self.make_sample("0")
        self.run_backfill()
        self.assertIsNone(self.experiment.ancestor_set_by_id)
        self.assertIsNone(self.experiment.ancestor_set_at)

    def test_an_experiment_with_no_a0_is_left_alone(self):
        self.make_sample("1")
        self.run_backfill()
        self.assertIsNone(self.experiment.ancestor_id)

    def test_ambiguity_is_skipped_rather_than_guessed(self):
        """Which of several A0 samples is the ancestor is a question about the data that only
        whoever ran the experiment can answer. Choosing one would be a silent wrong answer
        rather than a visible absent one."""
        self.make_sample("0", isolate_number="1")
        self.make_sample("0", isolate_number="2")
        self.run_backfill()
        self.assertIsNone(self.experiment.ancestor_id)

    def test_an_empty_a0_ale_designates_nothing(self):
        """The label alone is not a sample."""
        AleId.objects.create(ale_experiment=self.experiment, ale_id="0")
        self.run_backfill()
        self.assertIsNone(self.experiment.ancestor_id)

    def test_each_experiment_is_decided_on_its_own(self):
        other = AleExperiment.objects.create(instrument=Instrument.objects.create())
        mine = self.make_sample("0")

        self.experiment, keep = other, self.experiment
        self.make_sample("0", isolate_number="1")
        self.make_sample("0", isolate_number="2")
        self.experiment = keep

        self.run_backfill()
        other.refresh_from_db()
        self.assertEqual(self.experiment.ancestor_id, mine.id)
        self.assertIsNone(other.ancestor_id)

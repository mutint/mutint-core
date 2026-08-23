"""Mutation.ale_experiment on rows that predate the column.

0005 added the FK and filled it only for imports made after it, so everything
older kept NULL. That failed in the worst way -- silently: `./aledb reannotate`
filters on that FK, so it reported "has no mutations" for every experiment with
older data, and those mutations could never gain the annotation the Samples page
renders from.

0006 recovers the experiment from the observation chain. The rule is exercised
here against real imported data rather than a hand-built chain: the chain is five
models deep with required FKs along it, and what matters is that it holds for the
shape the importer actually produces.
"""

import os
import shutil
import tempfile
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_import import annotation, gd_import, reference, reference_store
from aledb_import.tests.test_annotation import _uploaded_as
from aledb_seq.migrations._backfill_rule import resolve
from aledb_seq.models import Mutation, ObservedMutation

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "aledb_import", "annotate", "tests", "fixtures")
SYNTHETIC_GFF3 = os.path.join(FIXTURES, "synthetic.gff3")
SYNTHETIC_GD = os.path.join(FIXTURES, "synthetic.gd")


class BackfillRuleTestCase(TestCase):

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        User.objects.create(username="tester", first_name="Test", last_name="User",
                            email="t@e.com", is_active=True, date_joined=datetime.now())

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]
        gff3_text, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences)
        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name="syn exp", person="tester")

        self.mutations = list(Mutation.objects.all())
        self.assertTrue(self.mutations, "fixture should import some mutations")

    def _unlink(self):
        """Put the rows back the way 0005 left them."""
        Mutation.objects.update(ale_experiment=None)
        return list(Mutation.objects.filter(
            ale_experiment__isnull=True).values_list("id", flat=True))

    def test_the_importer_links_new_mutations_itself(self):
        """The gap is historical: anything imported now arrives linked."""
        self.assertEqual(
            0, Mutation.objects.filter(ale_experiment__isnull=True).count())

    def test_it_recovers_the_experiment_for_unlinked_rows(self):
        unlinked = self._unlink()
        recovered = resolve(ObservedMutation, unlinked)

        self.assertEqual(len(unlinked), len(recovered))
        self.assertEqual({self.experiment.pk}, set(recovered.values()))

    def test_what_it_recovers_matches_what_the_importer_set(self):
        before = dict(Mutation.objects.values_list("id", "ale_experiment_id"))
        recovered = resolve(ObservedMutation, self._unlink())

        self.assertEqual(before, recovered)

    def test_a_mutation_with_no_observations_is_left_alone(self):
        orphan = Mutation.objects.create(position=999999)
        self.assertEqual({}, resolve(ObservedMutation, [orphan.id]))

    def test_a_mutation_seen_in_two_experiments_is_left_alone(self):
        """Mutations are meant to be per-experiment. Picking one of several would
        quietly attach it to the wrong one, so the row stays NULL."""
        other = gd_import._prepare_experiment(
            "syn project", "other exp", "tester", False)["experiment"]
        reference_store.establish_or_check(
            other, *reversed(list(reversed(reference.normalize_reference(SYNTHETIC_GFF3)))))
        gd_import.import_gd_files(
            [_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
            project_name="syn project", experiment_name="other exp", person="tester")

        shared = self.mutations[0]
        elsewhere = ObservedMutation.objects.exclude(
            mutation__ale_experiment=self.experiment).first()
        self.assertIsNotNone(elsewhere, "second import should produce observations")
        elsewhere.mutation = shared
        elsewhere.save()

        self._unlink()
        self.assertNotIn(shared.id, resolve(ObservedMutation, [shared.id]))

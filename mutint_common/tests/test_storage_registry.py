"""The storage registry: kinds, measuring, the rebuild, and clearing.

**Every assertion here is about kinds this test registered**, never the whole list: core
registers two and an assembled install adds a plugin's, so the list's contents are a fact
about the deployment. Same rule as the panel and nav tests.
"""

import os
import shutil
import tempfile

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common.models import DerivedDataState, StorageUsage
from mutint_common.rebuild_registry import get_rebuilders, is_stale
from mutint_common.storage_registry import (
    STORAGE_REBUILD, UNATTRIBUTED_REBUILD, NotClearable, UnknownStorageKind,
    bytes_by_experiment, bytes_by_kind, clear_kind, directory_bytes, ensure_measured,
    file_bytes, get_storage_kind, get_storage_kinds, is_clearable, measure_experiment,
    rebuild_storage, register_storage_kind, request_remeasure, stale_experiment_ids,
    total_bytes, unregister_storage_kind, usage_for,
)
from mutint_experiment.models import Experiment, Project


class StorageTestCase(TestCase):

    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")
        self.user = User.objects.create(username="s", email="s@e.com", is_active=True)
        self.project = Project.objects.create(name="P", user=self.user)
        self.experiment = Experiment.objects.create(name="E", project=self.project)

    def _register(self, key, measure, clear=None, **kwargs):
        register_storage_kind(self.app_config, key=key, label=kwargs.pop("label", key),
                              measure=measure, clear=clear, **kwargs)
        self.addCleanup(unregister_storage_kind, key)


class RegistrationTestCase(StorageTestCase):

    def test_registering_puts_the_kind_in_the_list(self):
        self._register("t_one", lambda e: 1)
        self.assertIn("t_one", [k["key"] for k in get_storage_kinds()])
        self.assertEqual("mutint_common", get_storage_kind("t_one")["app"])

    def test_the_same_key_twice_replaces_rather_than_doubles(self):
        self._register("t_two", lambda e: 1, label="First")
        self._register("t_two", lambda e: 1, label="Second")
        mine = [k for k in get_storage_kinds() if k["key"] == "t_two"]
        self.assertEqual(1, len(mine))
        self.assertEqual("Second", mine[0]["label"])

    def test_a_kind_with_no_clear_is_measured_only(self):
        self._register("t_ro", lambda e: 5)
        self.assertFalse(is_clearable("t_ro"))
        with self.assertRaises(NotClearable):
            clear_kind(self.experiment, "t_ro")

    def test_an_unknown_key_raises(self):
        with self.assertRaises(UnknownStorageKind):
            get_storage_kind("no_such_kind")
        with self.assertRaises(UnknownStorageKind):
            clear_kind(self.experiment, "no_such_kind")

    def test_measure_must_be_callable(self):
        with self.assertRaises(ValueError):
            register_storage_kind(self.app_config, key="t_bad", label="x", measure=3)

    def test_the_rebuilder_is_registered_by_core(self):
        self.assertIn(STORAGE_REBUILD, [r["name"] for r in get_rebuilders()])


class MeasuringTestCase(StorageTestCase):

    def test_a_raising_kind_counts_zero_and_the_others_still_count(self):
        def boom(experiment):
            raise RuntimeError("no")
        self._register("t_boom", boom)
        self._register("t_fine", lambda e: 42)
        with self.assertLogs("mutint_common.storage_registry", level="ERROR") as logs:
            sizes = measure_experiment(self.experiment)
        self.assertEqual(0, sizes["t_boom"])
        self.assertEqual(42, sizes["t_fine"])
        self.assertIn("t_boom", logs.output[0])

    def test_directory_bytes_sums_a_tree_and_tolerates_absence(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "a", "b"))
        with open(os.path.join(root, "a", "x"), "wb") as handle:
            handle.write(b"12345")
        with open(os.path.join(root, "a", "b", "y"), "wb") as handle:
            handle.write(b"1234567")
        self.assertEqual(12, directory_bytes(root))
        self.assertEqual(0, directory_bytes(os.path.join(root, "nothing")))
        self.assertEqual(5, file_bytes(os.path.join(root, "a", "x")))
        self.assertEqual(0, file_bytes(os.path.join(root, "a", "missing")))


class RebuildTestCase(StorageTestCase):

    def test_the_rebuild_writes_one_row_per_kind_and_prunes_unregistered_ones(self):
        StorageUsage.objects.create(experiment=self.experiment, kind="t_gone", bytes=9,
                                    measured_at="2020-01-01T00:00:00Z")
        self._register("t_a", lambda e: 10)
        self._register("t_b", lambda e: 20)
        rebuild_storage(self.experiment.id)
        mine = {r.kind: r.bytes for r in StorageUsage.objects.filter(
            experiment=self.experiment, kind__in=["t_a", "t_b", "t_gone"])}
        self.assertEqual({"t_a": 10, "t_b": 20}, mine)

    def test_a_missing_row_reads_as_zero(self):
        self._register("t_new", lambda e: 10)
        row = [u for u in usage_for(self.experiment) if u["key"] == "t_new"][0]
        self.assertEqual(0, row["bytes"])
        self.assertIsNone(row["measured_at"])
        self.assertTrue(row["clearable"] is False)

    def test_request_remeasure_marks_exactly_the_two_storage_names(self):
        rebuild_storage(self.experiment.id)
        from mutint_common.rebuild_registry import run_rebuilds
        run_rebuilds(self.experiment.id, only=[STORAGE_REBUILD])
        self.assertFalse(is_stale(STORAGE_REBUILD, self.experiment.id))
        request_remeasure(self.experiment.id)
        self.assertTrue(is_stale(STORAGE_REBUILD, self.experiment.id))
        # Not the dashboard's mutation counts: a cleared BAM changes no mutation.
        marked = set(DerivedDataState.objects.filter(
            stale_since__isnull=False).values_list("name", flat=True))
        self.assertNotIn("mutation_counts", marked)
        self.assertLessEqual(marked, {STORAGE_REBUILD, UNATTRIBUTED_REBUILD})

    def test_ensure_measured_and_stale_ids(self):
        self.assertEqual({self.experiment.id}, stale_experiment_ids([self.experiment.id]))
        self.assertTrue(ensure_measured(self.experiment.id))
        self.assertEqual(set(), stale_experiment_ids([self.experiment.id]))


class SumsTestCase(StorageTestCase):

    def test_sums_by_experiment_and_by_kind(self):
        other = Experiment.objects.create(name="F", project=self.project)
        self._register("t_x", lambda e: 100 if e.id == self.experiment.id else 1)
        self._register("t_y", lambda e: 5)
        rebuild_storage(self.experiment.id)
        rebuild_storage(other.id)
        # Only this test's kinds are summed into the expectation.
        by_exp = bytes_by_experiment([self.experiment.id, other.id])
        self.assertGreaterEqual(by_exp[self.experiment.id], 105)
        self.assertGreaterEqual(by_exp[other.id], 6)
        kinds = dict((k, s) for k, _, s in bytes_by_kind(
            Experiment.objects.filter(pk__in=[self.experiment.id, other.id])))
        self.assertEqual(101, kinds["t_x"])
        self.assertEqual(10, kinds["t_y"])
        self.assertGreaterEqual(total_bytes(Experiment.objects.filter(pk=other.id)), 6)


class ClearingTestCase(StorageTestCase):

    def test_clear_runs_the_callable_and_remeasures_eagerly(self):
        state = {"bytes": 50}
        cleared = []
        self._register("t_c", lambda e: state["bytes"],
                       clear=lambda e: (cleared.append(e.id), state.update(bytes=0)))
        freed = clear_kind(self.experiment, "t_c")
        self.assertEqual([self.experiment.id], cleared)
        self.assertEqual(50, freed)
        row = [u for u in usage_for(self.experiment) if u["key"] == "t_c"][0]
        self.assertEqual(0, row["bytes"])
        self.assertFalse(is_stale(STORAGE_REBUILD, self.experiment.id))

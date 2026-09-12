"""The seam that lets a component run something on the reference after it lands.

Core registers no annotator of its own -- the first is mutint-isescan -- so every test here
registers its own and unregisters it afterwards, and **every assertion is about this test's
own annotators, never about the whole list**: a shared registry's contents are a fact about
the deployment, the rule `test_panel_registry` learned under the assembled suite.
"""

import shutil
import tempfile

from django.apps import apps
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings

from mutint_common.annotator_registry import (
    AnnotatorOptionsInvalid,
    NoReference,
    clean_selections,
    get_reference_annotators,
    register_reference_annotator,
    render_annotator_panels,
    run_annotators,
    unregister_reference_annotator,
)
from mutint_import import breseq_folder
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Sample


def _run_ok(experiment, options, user):
    return {"message": "ran with %s" % sorted(options.items()), "extra": 1}


class RegistryTestCase(TestCase):
    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")

    def _register(self, name, **kwargs):
        kwargs.setdefault("label", name.title())
        kwargs.setdefault("run", _run_ok)
        kwargs.setdefault("template", "tests/annotator_panel.html")
        register_reference_annotator(self.app_config, name=name, **kwargs)
        self.addCleanup(unregister_reference_annotator, name)

    def _mine(self):
        return [a for a in get_reference_annotators() if a["name"].startswith("t_")]

    def test_registering_puts_the_annotator_in_the_list(self):
        self._register("t_one")
        self.assertEqual(["t_one"], [a["name"] for a in self._mine()])
        self.assertEqual("mutint_common", self._mine()[0]["app"])

    def test_registering_the_same_name_twice_replaces_rather_than_doubles(self):
        self._register("t_two", label="First")
        self._register("t_two", label="Second")
        mine = [a for a in self._mine() if a["name"] == "t_two"]
        self.assertEqual(1, len(mine))
        self.assertEqual("Second", mine[0]["label"])

    def test_unregistering_removes_it(self):
        self._register("t_gone")
        unregister_reference_annotator("t_gone")
        self.assertEqual([], self._mine())


class SelectionsTestCase(RegistryTestCase):
    """`clean_selections`: the page's payload into what `run` is handed."""

    def test_only_ticked_entries_survive_and_enabled_is_stripped(self):
        self._register("t_a")
        self._register("t_b")
        selections = clean_selections({"t_a": {"enabled": True, "depth": "3"},
                                       "t_b": {"enabled": False, "depth": "9"}})
        self.assertEqual({"t_a": {"depth": "3"}}, selections)

    def test_registry_order_wins_over_payload_order(self):
        self._register("t_first")
        self._register("t_second")
        selections = clean_selections({"t_second": {"enabled": True},
                                       "t_first": {"enabled": True}})
        self.assertEqual(["t_first", "t_second"], list(selections))

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(AnnotatorOptionsInvalid) as caught:
            clean_selections({"t_nobody": {"enabled": True}})
        self.assertIn("t_nobody", str(caught.exception))

    def test_a_payload_that_is_not_a_mapping_is_refused(self):
        self._register("t_a")
        with self.assertRaises(AnnotatorOptionsInvalid):
            clean_selections(["t_a"])
        with self.assertRaises(AnnotatorOptionsInvalid):
            clean_selections({"t_a": "yes"})
        self.assertEqual({}, clean_selections(None))

    def test_clean_decides_what_the_strings_mean(self):
        self._register("t_c", clean=lambda options: {"depth": int(options.get("depth", 0))})
        self.assertEqual({"t_c": {"depth": 3}},
                         clean_selections({"t_c": {"enabled": True, "depth": "3"}}))

    def test_a_value_error_from_clean_names_the_annotator(self):
        def clean(options):
            raise ValueError("depth must be a number")
        self._register("t_d", label="Depth Thing", clean=clean)
        with self.assertRaises(AnnotatorOptionsInvalid) as caught:
            clean_selections({"t_d": {"enabled": True, "depth": "x"}})
        self.assertEqual("Depth Thing: depth must be a number", str(caught.exception))

    def test_any_other_error_from_clean_is_a_bug_and_propagates(self):
        def clean(options):
            raise KeyError("oops")
        self._register("t_e", clean=clean)
        with self.assertRaises(KeyError):
            clean_selections({"t_e": {"enabled": True}})


class ExperimentTestCase(TestCase):
    """An experiment with a reference, for rendering and running against."""

    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.user = User.objects.create(username="ann", email="a@e.com", is_active=True)
        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", owner_name="ann")
        self.experiment = Sample.objects.get().experiment
        self.request = RequestFactory().get("/import/")
        self.request.user = self.user

    def _register(self, name, **kwargs):
        kwargs.setdefault("label", name.title())
        kwargs.setdefault("run", _run_ok)
        kwargs.setdefault("template", "tests/annotator_panel.html")
        register_reference_annotator(self.app_config, name=name, **kwargs)
        self.addCleanup(unregister_reference_annotator, name)

    def _mine(self, rendered):
        return [p for p in rendered if p["name"].startswith("t_")]


class RenderTestCase(ExperimentTestCase):
    def test_the_context_callable_gets_the_experiment_and_the_request(self):
        seen = {}

        def context(experiment, request):
            seen["args"] = (experiment, request)
            return {"note": "hello"}

        self._register("t_r", context=context, description="Does a thing.")
        panels = self._mine(render_annotator_panels(self.experiment, self.request))

        self.assertEqual((self.experiment, self.request), seen["args"])
        self.assertEqual(1, len(panels))
        self.assertEqual("Does a thing.", panels[0]["description"])
        # The name is in the context, so a template can build ids and endpoints from it.
        self.assertIn("t_r: hello", panels[0]["html"])

    def test_a_panel_that_raises_is_dropped_and_the_rest_render(self):
        self._register("t_bad", template="tests/annotator_raises.html")
        self._register("t_good")
        with self.assertLogs("mutint_common.annotator_registry", level="ERROR"):
            panels = self._mine(render_annotator_panels(self.experiment, self.request))
        self.assertEqual(["t_good"], [p["name"] for p in panels])


class RunTestCase(ExperimentTestCase):
    def test_each_selected_annotator_runs_and_reports_a_row(self):
        seen = []

        def run(experiment, options, user):
            seen.append((experiment.pk, options, user))
            return {"message": "queued as job 1", "run_id": 7}

        self._register("t_run", label="Runner", run=run)
        self._register("t_idle")
        rows = run_annotators(self.experiment, {"t_run": {"depth": 3}}, self.user)

        self.assertEqual([(self.experiment.pk, {"depth": 3}, self.user)], seen)
        self.assertEqual([{"name": "t_run", "label": "Runner", "message": "queued as job 1",
                           "run_id": 7, "error": None}], rows)

    def test_one_raising_annotator_is_an_error_row_and_the_rest_still_run(self):
        def boom(experiment, options, user):
            raise RuntimeError("tool exploded")

        self._register("t_boom", run=boom)
        self._register("t_fine")
        with self.assertLogs("mutint_common.annotator_registry", level="ERROR"):
            rows = run_annotators(self.experiment, {"t_boom": {}, "t_fine": {}}, self.user)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual("tool exploded", by_name["t_boom"]["error"])
        self.assertIsNone(by_name["t_fine"]["error"])
        self.assertIn("ran with", by_name["t_fine"]["message"])

    def test_a_run_returning_nothing_is_a_row_with_an_empty_message(self):
        self._register("t_quiet", run=lambda e, o, u: None)
        rows = run_annotators(self.experiment, {"t_quiet": {}}, self.user)
        self.assertEqual("", rows[0]["message"])
        self.assertIsNone(rows[0]["error"])

    def test_no_reference_is_refused_before_anything_runs(self):
        from mutint_sample.models import ReferenceSequences

        seen = []
        self._register("t_x", run=lambda e, o, u: seen.append(1))
        ReferenceSequences.objects.filter(experiment=self.experiment).delete()
        with self.assertRaises(NoReference):
            run_annotators(self.experiment, {"t_x": {}}, self.user)
        self.assertEqual([], seen)

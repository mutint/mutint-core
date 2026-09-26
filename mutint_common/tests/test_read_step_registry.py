"""The seam that lets a component do something to a sample's reads before a producer uses them.

Core has no reads and no producer, so every test registers its own steps and unregisters them
afterwards, and asserts only about those: a deployment's own steps (mutint-breseq's `trim`,
say) are in the same list, and what core may assert is what core put there.
"""

import tempfile

from django.apps import apps
from django.test import SimpleTestCase

from mutint_common import read_step_registry as registry
from mutint_common.read_step_registry import (
    STAGE_INSPECT,
    STAGE_TRANSFORM,
    ReadStepContext,
    attach_read_steps,
    clean_selection,
    discard_read_steps,
    register_read_step,
    run_read_steps,
    unregister_read_step,
)
from mutint_jobs import processes


class RegistryTestCase(SimpleTestCase):

    def setUp(self):
        self.app_config = apps.get_app_config("mutint_common")
        self.calls = []

    def _register(self, name, stage, run=None, **kwargs):
        register_read_step(self.app_config, name, name.title(),
                           run or (lambda ctx: self.calls.append(name)),
                           stage=stage, **kwargs)
        self.addCleanup(unregister_read_step, name)

    def _ctx(self, reads=("a.fastq",), **kwargs):
        return ReadStepContext(experiment=None, sample_name="s", reads=list(reads),
                               paired=True, producer="test:1", work_dir=tempfile.mkdtemp(),
                               **kwargs)

    def test_an_inspect_step_runs_before_a_transform_step_registered_earlier(self):
        """The stage decides, not the registration order: QC must see the reads as they
        arrived, whichever app happens to load first."""
        self._register("t_trim", STAGE_TRANSFORM)
        self._register("t_qc", STAGE_INSPECT)

        run_read_steps(clean_selection(["t_trim", "t_qc"]), self._ctx())

        self.assertEqual(["t_qc", "t_trim"], self.calls)

    def test_within_a_stage_registration_order_holds(self):
        self._register("t_one", STAGE_INSPECT)
        self._register("t_two", STAGE_INSPECT)

        run_read_steps(clean_selection(["t_two", "t_one"]), self._ctx())

        self.assertEqual(["t_one", "t_two"], self.calls)

    def test_a_transform_step_replaces_the_reads_the_next_step_sees(self):
        seen = []
        self._register("t_trim", STAGE_TRANSFORM, run=lambda ctx: ["trimmed.fastq"])
        self._register("t_after", STAGE_TRANSFORM, run=lambda ctx: seen.append(ctx.reads))

        reads = run_read_steps(clean_selection(["t_trim", "t_after"]), self._ctx())

        self.assertEqual([["trimmed.fastq"]], seen)
        self.assertEqual(["trimmed.fastq"], reads)

    def test_what_an_inspect_step_returns_is_ignored(self):
        self._register("t_qc", STAGE_INSPECT, run=lambda ctx: ["something-else.fastq"])

        reads = run_read_steps(clean_selection(["t_qc"]), self._ctx(reads=["a.fastq"]))

        self.assertEqual(["a.fastq"], reads)

    def test_a_step_not_selected_does_not_run(self):
        self._register("t_qc", STAGE_INSPECT)

        run_read_steps(clean_selection([]), self._ctx())

        self.assertEqual([], self.calls)

    def test_an_unknown_name_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            clean_selection(["t_no_such_step"])
        self.assertIn("t_no_such_step", str(caught.exception))

    def test_an_unavailable_step_is_refused_only_when_asked(self):
        """A launch asks, so a missing tool is caught before an upload is claimed. A worker
        does not: the step's own run is what answers there."""
        self._register("t_qc", STAGE_INSPECT, available=lambda: (False, "no fastqc"))

        self.assertEqual(["t_qc"], [s.name for s in clean_selection(["t_qc"])])
        with self.assertRaises(ValueError) as caught:
            clean_selection(["t_qc"], check_available=True)
        self.assertIn("no fastqc", str(caught.exception))

    def test_an_availability_check_that_raises_reads_as_unavailable(self):
        def broken():
            raise RuntimeError("boom")
        self._register("t_qc", STAGE_INSPECT, available=broken)

        ok, why = registry.get_step("t_qc").available()

        self.assertFalse(ok)
        self.assertIn("boom", why)

    def test_registering_a_name_again_replaces_it(self):
        self._register("t_qc", STAGE_INSPECT)
        self._register("t_qc", STAGE_TRANSFORM)

        mine = [s for s in registry.steps() if s.name == "t_qc"]
        self.assertEqual(1, len(mine))
        self.assertEqual(STAGE_TRANSFORM, mine[0].stage)

    def test_an_unknown_stage_is_refused_at_registration(self):
        with self.assertRaises(ValueError):
            register_read_step(self.app_config, "t_bad", "Bad", lambda ctx: None,
                               stage="sometimes")

    def test_a_cancel_between_steps_stops_the_rest(self):
        flag = {"cancelled": False}

        def first(ctx):
            self.calls.append("first")
            flag["cancelled"] = True
        self._register("t_first", STAGE_INSPECT, run=first)
        self._register("t_second", STAGE_TRANSFORM)

        with self.assertRaises(processes.Cancelled):
            run_read_steps(clean_selection(["t_first", "t_second"]),
                           self._ctx(is_cancelled=lambda: flag["cancelled"]))

        self.assertEqual(["first"], self.calls)

    def test_a_step_failing_propagates(self):
        def fails(ctx):
            raise registry.ReadStepFailed("no good")
        self._register("t_fail", STAGE_TRANSFORM, run=fails)

        with self.assertRaises(registry.ReadStepFailed):
            run_read_steps(clean_selection(["t_fail"]), self._ctx())

    def test_attach_and_discard_reach_each_step_and_one_raising_does_not_stop_another(self):
        def broken(*args):
            raise RuntimeError("boom")
        self._register("t_broken", STAGE_INSPECT, attach=broken, discard=broken)
        self._register("t_fine", STAGE_INSPECT,
                       attach=lambda producer, sample: self.calls.append(("attach", sample)),
                       discard=lambda producer: self.calls.append(("discard", producer)))

        with self.assertLogs("mutint_common.read_step_registry", level="ERROR"):
            attach_read_steps(["t_broken", "t_fine"], "test:1", "the-sample")
        with self.assertLogs("mutint_common.read_step_registry", level="ERROR"):
            discard_read_steps(["t_broken", "t_fine", "t_uninstalled"], "test:1")

        self.assertEqual([("attach", "the-sample"), ("discard", "test:1")], self.calls)

    def test_scratch_dirs_are_under_the_producers_run(self):
        ctx = self._ctx()
        path = ctx.scratch_dir("qc")
        self.assertTrue(path.startswith(ctx.work_dir))

    def test_notes_go_to_the_producer_when_it_takes_them(self):
        noted = []
        ctx = self._ctx(note=noted.append)
        ctx.note("look at this")
        self.assertEqual(["look at this"], noted)

    def test_a_context_with_no_log_writes_nowhere_rather_than_raising(self):
        self._ctx().note("nobody is listening")

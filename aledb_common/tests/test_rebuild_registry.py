"""The rebuild bus: registering, marking stale, and running.

The registry is module-level state shared with every installed app, so every test here
registers under a name of its own and removes it again -- asserting on what the *installed*
set contains would be a statement about the deployment rather than about this code, which is
the mistake `aledb-core/CLAUDE.md` records three tests having made with the nav registry.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from aledb_common.models import DerivedDataState
from aledb_common.rebuild_registry import (
    EXPERIMENT_SCOPE, PRIORITY_AGGREGATE, PRIORITY_SETTINGS, SITE_SCOPE,
    ensure_fresh, get_rebuilder, get_rebuilders, is_stale, register_rebuilder,
    request_rebuild, run_rebuilds, unregister_rebuilder,
)
from aledb_experiment.models import AleExperiment


class RebuildRegistryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])
        self.calls = []

    def _register(self, name, fn=None, **kwargs):
        register_rebuilder(name, fn or (lambda *a: self.calls.append((name,) + a)), **kwargs)
        self.addCleanup(unregister_rebuilder, name)
        return name

    # ---- registering ----------------------------------------------------------------
    def test_a_duplicate_name_is_refused(self):
        """Two rebuilds sharing a name would share a staleness row, and each would keep
        marking the other current."""
        self._register("test.dup")
        with self.assertRaises(ValueError):
            self._register("test.dup")

    def test_a_bad_scope_is_refused(self):
        with self.assertRaises(ValueError):
            self._register("test.scope", scope="whenever")

    def test_something_that_is_not_callable_is_refused(self):
        with self.assertRaises(ValueError):
            self._register("test.notcallable", fn="rebuild_please")

    def test_priority_orders_ahead_of_registration(self):
        """The reason this registry has explicit ordering and nav_registry does not.

        `aledb_dashboard` is the second app in the aledb_* block because that is where its
        *nav entry* belongs, but its totals are counted across every experiment and so must
        run last. INSTALLED_APPS order cannot say both.
        """
        self._register("test.late", priority=PRIORITY_AGGREGATE)
        self._register("test.early", priority=PRIORITY_SETTINGS)

        names = [r["name"] for r in get_rebuilders() if r["name"].startswith("test.")]
        self.assertEqual(["test.early", "test.late"], names)

    def test_equal_priorities_keep_registration_order(self):
        self._register("test.first")
        self._register("test.second")

        names = [r["name"] for r in get_rebuilders() if r["name"].startswith("test.")]
        self.assertEqual(["test.first", "test.second"], names)

    def test_an_unknown_name_in_only_is_skipped_not_raised(self):
        """`rebuild_after_structural_change` asks for 'aledb_fixation' from a repo that
        cannot know whether the plugin is installed. A deployment without it has less to do,
        not an exception."""
        self.assertEqual([], get_rebuilders(only=["test.never_registered"]))

    # ---- staleness ------------------------------------------------------------------
    def test_never_built_reads_as_stale(self):
        """No row is the same answer as an invalidated row, so a newly registered rebuild
        needs no backfill and a new experiment needs no seeding."""
        self._register("test.fresh")
        self.assertTrue(is_stale("test.fresh", self.experiment.ale_id))
        self.assertFalse(DerivedDataState.objects.filter(name="test.fresh").exists())

    def test_running_it_makes_it_current(self):
        self._register("test.run")
        run_rebuilds(self.experiment.ale_id, only=["test.run"])

        self.assertFalse(is_stale("test.run", self.experiment.ale_id))
        self.assertEqual([("test.run", self.experiment.ale_id)], self.calls)

    def test_requesting_a_rebuild_makes_it_stale_again(self):
        self._register("test.again")
        run_rebuilds(self.experiment.ale_id, only=["test.again"])
        request_rebuild(self.experiment.ale_id, only=["test.again"])

        self.assertTrue(is_stale("test.again", self.experiment.ale_id))

    def test_requesting_with_no_experiment_marks_every_experiment(self):
        """The global-filter case: every experiment's counts are computed through it."""
        second = self.client.post(
            "/ale/projects/create/", {"name": "P2", "experiment": "E2"}).json()
        other = AleExperiment.objects.get(pk=second["experiment_id"])

        self._register("test.global")
        run_rebuilds(self.experiment.ale_id, only=["test.global"])
        run_rebuilds(other.ale_id, only=["test.global"])
        request_rebuild(only=["test.global"])

        self.assertTrue(is_stale("test.global", self.experiment.ale_id))
        self.assertTrue(is_stale("test.global", other.ale_id))

    def test_only_narrows_what_is_marked(self):
        self._register("test.wanted")
        self._register("test.untouched")
        run_rebuilds(self.experiment.ale_id, only=["test.wanted", "test.untouched"])
        request_rebuild(self.experiment.ale_id, only=["test.wanted"])

        self.assertTrue(is_stale("test.wanted", self.experiment.ale_id))
        self.assertFalse(is_stale("test.untouched", self.experiment.ale_id))

    def test_a_current_rebuild_is_not_run_again(self):
        self._register("test.skip")
        run_rebuilds(self.experiment.ale_id, only=["test.skip"])
        run_rebuilds(self.experiment.ale_id, only=["test.skip"])

        self.assertEqual(1, len(self.calls))

    def test_force_runs_it_anyway(self):
        self._register("test.force")
        run_rebuilds(self.experiment.ale_id, only=["test.force"])
        run_rebuilds(self.experiment.ale_id, only=["test.force"], force=True)

        self.assertEqual(2, len(self.calls))

    # ---- ensure_fresh ---------------------------------------------------------------
    def test_ensure_fresh_builds_once_and_then_does_nothing(self):
        """What the Overview calls: the first view after a change pays, the rest do not."""
        self._register("test.ensure")
        self.assertTrue(ensure_fresh("test.ensure", self.experiment.ale_id))
        self.assertTrue(ensure_fresh("test.ensure", self.experiment.ale_id))

        self.assertEqual(1, len(self.calls))

    # ---- scope ----------------------------------------------------------------------
    def test_a_site_scoped_rebuild_takes_no_experiment(self):
        self._register("test.site", fn=lambda: self.calls.append(("test.site",)),
                       scope=SITE_SCOPE)
        run_rebuilds(self.experiment.ale_id, only=["test.site"])

        self.assertEqual([("test.site",)], self.calls)
        self.assertFalse(is_stale("test.site"))

    def test_an_experiment_scoped_rebuild_is_skipped_by_a_site_sweep(self):
        """`run_rebuilds(None)` cannot answer "which experiment", so it runs only the
        rebuilds that do not need one."""
        self._register("test.per_experiment", scope=EXPERIMENT_SCOPE)
        run_rebuilds(None, only=["test.per_experiment"])

        self.assertEqual([], self.calls)

    # ---- failures -------------------------------------------------------------------
    def _explode(self, *args):
        raise RuntimeError("the rebuild went wrong")

    def test_a_failure_is_isolated_and_the_others_still_run(self):
        """The behaviour change from `run_post_experiment_hooks`, which was a bare loop: one
        plugin raising aborted the rest and 500'd an import whose mutations were already
        committed."""
        self._register("test.explodes", fn=self._explode, priority=PRIORITY_SETTINGS)
        self._register("test.survives")

        results = run_rebuilds(self.experiment.ale_id,
                               only=["test.explodes", "test.survives"])

        self.assertEqual({"test.explodes": False, "test.survives": True}, results)
        self.assertEqual([("test.survives", self.experiment.ale_id)], self.calls)

    def test_a_failure_leaves_the_data_stale(self):
        """Recoverable, and recorded: the next reader tries again and `--list` shows why."""
        self._register("test.fails", fn=self._explode)
        run_rebuilds(self.experiment.ale_id, only=["test.fails"], force=True)

        self.assertTrue(is_stale("test.fails", self.experiment.ale_id))
        state = DerivedDataState.objects.get(name="test.fails")
        self.assertIn("the rebuild went wrong", state.last_error)

    def test_a_failure_does_not_propagate_out_of_ensure_fresh(self):
        """A page whose derived data will not rebuild should render what it has, not 500."""
        self._register("test.page_fails", fn=self._explode)
        self.assertFalse(ensure_fresh("test.page_fails", self.experiment.ale_id))

    def test_a_successful_rebuild_clears_a_previous_error(self):
        outcomes = iter([self._explode, lambda experiment_id: None])
        self._register("test.recovers", fn=lambda experiment_id: next(outcomes)(experiment_id))
        run_rebuilds(self.experiment.ale_id, only=["test.recovers"], force=True)
        run_rebuilds(self.experiment.ale_id, only=["test.recovers"], force=True)

        state = DerivedDataState.objects.get(name="test.recovers")
        self.assertEqual("", state.last_error)
        self.assertIsNone(state.stale_since)

    def test_being_invalidated_mid_rebuild_leaves_it_stale(self):
        """The rebuild that just finished never saw the second change, so clearing the flag
        would lose it silently."""
        experiment_id = self.experiment.ale_id

        def invalidate_myself(_experiment_id):
            request_rebuild(experiment_id, only=["test.racy"], reason="changed again")

        self._register("test.racy", fn=invalidate_myself)
        run_rebuilds(experiment_id, only=["test.racy"], force=True)

        self.assertTrue(is_stale("test.racy", experiment_id))

    # ---- the plugin-facing alias ----------------------------------------------------
    def test_register_post_experiment_hook_still_works(self):
        """aledb-fixation and aledb-converge call this and were not edited."""
        from aledb_common.plugin_registry import (
            register_post_experiment_hook, run_post_experiment_hooks,
        )

        seen = []
        name = register_post_experiment_hook(lambda exp_id: seen.append(exp_id))
        self.addCleanup(unregister_rebuilder, name)

        self.assertIsNotNone(get_rebuilder(name))
        run_post_experiment_hooks(self.experiment.ale_id)
        self.assertEqual([self.experiment.ale_id], seen)

    def test_two_anonymous_hooks_from_one_module_both_register(self):
        """The old API was a list and allowed it; deriving a name from the module must not
        turn that into an error."""
        from aledb_common.plugin_registry import register_post_experiment_hook

        first = register_post_experiment_hook(lambda exp_id: None)
        self.addCleanup(unregister_rebuilder, first)
        second = register_post_experiment_hook(lambda exp_id: None)
        self.addCleanup(unregister_rebuilder, second)

        self.assertNotEqual(first, second)
        self.assertIsNotNone(get_rebuilder(second))


class RebuildCommandTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

    def _run(self, *args, **options):
        out = StringIO()
        call_command("rebuild", *args, stdout=out, stderr=out, **options)
        return out.getvalue()

    def test_list_names_what_is_registered(self):
        output = self._run("--list")
        for name in ("experiment_filter", "overview", "static_data", "sample_counts"):
            self.assertIn(name, output)

    def test_list_says_when_nothing_has_ever_been_built(self):
        """"Nothing stale" and "nothing recorded at all" look identical in a count column."""
        self.assertIn("never built", self._run("--list"))

    def test_a_bad_experiment_id_is_refused(self):
        with self.assertRaises(CommandError):
            self._run("999999")

    def test_an_unknown_only_name_is_refused(self):
        """Unlike `only=` in code, which skips an uninstalled plugin: a name typed at a shell
        that silently does nothing is indistinguishable from having nothing to do."""
        with self.assertRaises(CommandError):
            self._run("--all", only=["not_a_rebuild"])

    def test_it_needs_an_experiment_or_all(self):
        with self.assertRaises(CommandError):
            self._run()

    def test_naming_an_experiment_and_all_is_refused(self):
        with self.assertRaises(CommandError):
            self._run(str(self.experiment.ale_id), "--all")

    def test_it_rebuilds_the_named_experiment(self):
        from aledb_stats.models import ExperimentSummary

        self._run(str(self.experiment.ale_id), only=["overview"])
        self.assertTrue(
            ExperimentSummary.objects.filter(ale_experiment=self.experiment).exists())

    def test_all_rebuilds_every_live_experiment(self):
        from aledb_stats.models import ExperimentSummary

        second = self.client.post(
            "/ale/projects/create/", {"name": "P2", "experiment": "E2"}).json()
        other = AleExperiment.objects.get(pk=second["experiment_id"])

        self._run("--all", only=["overview"])
        self.assertEqual(2, ExperimentSummary.objects.count())
        self.assertTrue(ExperimentSummary.objects.filter(ale_experiment=other).exists())

    def test_all_leaves_a_soft_deleted_experiment_alone(self):
        """Rebuilding one is work whose result nobody can see."""
        from aledb_stats.models import ExperimentSummary
        from django.utils import timezone

        AleExperiment.objects.filter(pk=self.experiment.pk).update(
            deleted_at=timezone.now())
        self._run("--all", only=["overview"])

        self.assertFalse(
            ExperimentSummary.objects.filter(ale_experiment=self.experiment).exists())

    def test_a_second_run_reports_everything_current(self):
        self._run(str(self.experiment.ale_id), only=["overview"])
        self.assertIn("0 rebuilt", self._run(str(self.experiment.ale_id), only=["overview"]))


class DeclaredInputsTestCase(TestCase):
    """A rebuild says what it reads, and a caller says what changed.

    Without this, `request_rebuild` marks everything registered, and a frequency-cutoff edit
    invalidated derived data that never reads through the filter -- `aledb_phylogeny` queries
    ObservedMutation directly, so its tree cannot have changed. On a page that hides its own
    content when marked stale, that is a warning asking for a rebuild which would redraw the
    identical answer, and warnings people learn to ignore are worse than none.
    """

    # Its own fixture rather than subclassing RebuildRegistryTestCase: inheriting a TestCase
    # re-runs every test it declares, which is how this file briefly grew by 44 tests that
    # were all copies of ones already running.
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])
        self.calls = []

    def _register(self, name, fn=None, **kwargs):
        register_rebuilder(name, fn or (lambda *a: self.calls.append((name,) + a)), **kwargs)
        self.addCleanup(unregister_rebuilder, name)
        return name

    def test_a_registration_that_says_nothing_reads_everything(self):
        """The default has to be the conservative one: an existing plugin that knows nothing
        about this must keep being marked by every caller."""
        from aledb_common.rebuild_registry import ALL_INPUTS

        self.assertEqual(ALL_INPUTS, get_rebuilder(self._register("test.default"))["inputs"])

    def test_a_filter_change_marks_what_reads_the_filter(self):
        """Both started fresh, deliberately. A missing DerivedDataState row already counts as
        stale, so asserting `assertFalse(is_stale(...))` on a never-built rebuild would fail
        whatever this code did -- and asserting the opposite would pass whatever it did."""
        from aledb_common.rebuild_registry import INPUT_FILTERS, INPUT_MUTATIONS

        both = self._register("test.both")
        muts = self._register("test.muts", inputs={INPUT_MUTATIONS})
        run_rebuilds(self.experiment.ale_id, force=True)
        self.assertFalse(is_stale(muts, self.experiment.ale_id), "fresh to begin with")

        request_rebuild(self.experiment.ale_id, changed=INPUT_FILTERS, reason='test')

        self.assertTrue(is_stale(both, self.experiment.ale_id))
        self.assertFalse(is_stale(muts, self.experiment.ale_id),
                         "declared independent of the filters, so a cutoff cannot stale it")

    def test_no_changed_still_marks_everything(self):
        """An import and a mutation edit both mean "all of it", and both pass nothing."""
        from aledb_common.rebuild_registry import INPUT_MUTATIONS

        muts = self._register("test.muts2", inputs={INPUT_MUTATIONS})
        run_rebuilds(self.experiment.ale_id, force=True)
        self.assertFalse(is_stale(muts, self.experiment.ale_id), "fresh to begin with")

        request_rebuild(self.experiment.ale_id, reason='test')

        self.assertTrue(is_stale(muts, self.experiment.ale_id))

    def test_an_unknown_input_is_refused_at_registration(self):
        """Silently narrowing what gets marked is data that quietly stops being refreshed --
        the same reason `./aledb rebuild --only` refuses a name nothing registered."""
        with self.assertRaises(ValueError):
            self._register("test.typo", inputs={"filtres"})

    def test_an_unknown_changed_is_refused(self):
        with self.assertRaises(ValueError):
            request_rebuild(self.experiment.ale_id, changed="filtres", reason='test')

    def test_registering_hands_back_the_name(self):
        """A plugin stores it and asks `is_stale` for it later, rather than writing out a
        name the registry derives."""
        self.assertEqual("test.returned",
                         register_rebuilder("test.returned", lambda _: None))
        self.addCleanup(unregister_rebuilder, "test.returned")


class ManualRebuildTestCase(TestCase):
    """`auto=False`: tracked and marked, never run behind anyone's back.

    It is what lets derived data be told it has gone stale without promising to recompute
    itself. aledb-phylogeny wanted exactly that and could not have it, so it registered nothing
    and its stored tree was never invalidated at all.
    """

    # Its own fixture rather than subclassing RebuildRegistryTestCase: inheriting a TestCase
    # re-runs every test it declares, which is how this file briefly grew by 44 tests that
    # were all copies of ones already running.
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])
        self.calls = []

    def _register(self, name, fn=None, **kwargs):
        register_rebuilder(name, fn or (lambda *a: self.calls.append((name,) + a)), **kwargs)
        self.addCleanup(unregister_rebuilder, name)
        return name

    def test_it_is_not_run_by_a_plain_rebuild(self):
        name = self._register("test.manual", auto=False)

        run_rebuilds(self.experiment.ale_id)

        self.assertEqual([], self.calls)

    def test_force_does_not_override_it(self):
        """`run_post_experiment_hooks` forces on every import. If force reached this, an
        import would build every opted-out thing there is, which is the whole thing being
        opted out of. Force means "even if fresh", not "even if you opted out"."""
        self._register("test.manual2", auto=False)

        run_rebuilds(self.experiment.ale_id, force=True)

        self.assertEqual([], self.calls)

    def test_naming_it_runs_it(self):
        """`./aledb rebuild 4 --only aledb_phylogeny` is the ask that builds one."""
        name = self._register("test.manual3", auto=False)

        run_rebuilds(self.experiment.ale_id, only=[name], force=True)

        self.assertEqual([(name, self.experiment.ale_id)], self.calls)

    def test_it_is_still_marked_stale(self):
        """The point of registering at all. Marking is what its page reads."""
        name = self._register("test.manual4", auto=False)

        request_rebuild(self.experiment.ale_id, reason='test')

        self.assertTrue(is_stale(name, self.experiment.ale_id))

    def test_a_plain_rebuild_leaves_it_stale(self):
        """And does not report having done anything about it."""
        name = self._register("test.manual5", auto=False)
        request_rebuild(self.experiment.ale_id, reason='test')

        results = run_rebuilds(self.experiment.ale_id)

        self.assertNotIn(name, results)
        self.assertTrue(is_stale(name, self.experiment.ale_id))

    def test_the_listing_says_it_is_manual(self):
        """A bare `./aledb rebuild 4` leaving it stale is the design, so the listing has to
        say so or the stale marker beside it reads as a failure."""
        name = self._register("test.manual6", auto=False)
        out = StringIO()

        call_command("rebuild", "--list", stdout=out)

        listed = [line for line in out.getvalue().splitlines() if line.startswith(name)]
        self.assertEqual(1, len(listed), out.getvalue())
        self.assertIn("manual", listed[0])

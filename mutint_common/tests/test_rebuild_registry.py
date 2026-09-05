"""The rebuild bus: registering, marking stale, and running.

The registry is module-level state shared with every installed app, so every test here
registers under a name of its own and removes it again -- asserting on what the *installed*
set contains would be a statement about the deployment rather than about this code, which is
the mistake `mutint-core/CLAUDE.md` records three tests having made with the nav registry.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from mutint_common.models import DerivedDataState
from mutint_common.rebuild_registry import (
    EXPERIMENT_SCOPE, PRIORITY_AGGREGATE, PRIORITY_SETTINGS, SITE_SCOPE,
    ensure_fresh, get_rebuilder, get_rebuilders, is_stale, register_rebuilder,
    request_rebuild, run_rebuilds, unregister_rebuilder,
)
from mutint_experiment.models import Experiment


class RebuildRegistryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])
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

        `mutint_dashboard` is the second app in the mutint_* block because that is where its
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
        """A repo naming a plugin's rebuild cannot know whether the plugin is installed, so a
        deployment without it has less to do rather than an exception.

        `rebuild_after_structural_change` asking for 'mutint_fixation' was the motivating
        caller and no longer exists -- fixation stores nothing to rebuild. The property is
        the contract `only=` offers, and the distinction it draws against
        `./mutint rebuild --only`, which *refuses* an unknown name because a typo at a shell
        that silently does nothing is indistinguishable from having nothing to do."""
        self.assertEqual([], get_rebuilders(only=["test.never_registered"]))

    # ---- staleness ------------------------------------------------------------------
    def test_never_built_reads_as_stale(self):
        """No row is the same answer as an invalidated row, so a newly registered rebuild
        needs no backfill and a new experiment needs no seeding."""
        self._register("test.fresh")
        self.assertTrue(is_stale("test.fresh", self.experiment.id))
        self.assertFalse(DerivedDataState.objects.filter(name="test.fresh").exists())

    def test_running_it_makes_it_current(self):
        self._register("test.run")
        run_rebuilds(self.experiment.id, only=["test.run"])

        self.assertFalse(is_stale("test.run", self.experiment.id))
        self.assertEqual([("test.run", self.experiment.id)], self.calls)

    def test_requesting_a_rebuild_makes_it_stale_again(self):
        self._register("test.again")
        run_rebuilds(self.experiment.id, only=["test.again"])
        request_rebuild(self.experiment.id, only=["test.again"])

        self.assertTrue(is_stale("test.again", self.experiment.id))

    def test_requesting_with_no_experiment_marks_every_experiment(self):
        """The global-filter case: every experiment's counts are computed through it."""
        second = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        other = Experiment.objects.get(pk=second["experiment_id"])

        self._register("test.global")
        run_rebuilds(self.experiment.id, only=["test.global"])
        run_rebuilds(other.id, only=["test.global"])
        request_rebuild(only=["test.global"])

        self.assertTrue(is_stale("test.global", self.experiment.id))
        self.assertTrue(is_stale("test.global", other.id))

    def test_only_narrows_what_is_marked(self):
        self._register("test.wanted")
        self._register("test.untouched")
        run_rebuilds(self.experiment.id, only=["test.wanted", "test.untouched"])
        request_rebuild(self.experiment.id, only=["test.wanted"])

        self.assertTrue(is_stale("test.wanted", self.experiment.id))
        self.assertFalse(is_stale("test.untouched", self.experiment.id))

    def test_a_current_rebuild_is_not_run_again(self):
        self._register("test.skip")
        run_rebuilds(self.experiment.id, only=["test.skip"])
        run_rebuilds(self.experiment.id, only=["test.skip"])

        self.assertEqual(1, len(self.calls))

    def test_force_runs_it_anyway(self):
        self._register("test.force")
        run_rebuilds(self.experiment.id, only=["test.force"])
        run_rebuilds(self.experiment.id, only=["test.force"], force=True)

        self.assertEqual(2, len(self.calls))

    # ---- ensure_fresh ---------------------------------------------------------------
    def test_ensure_fresh_builds_once_and_then_does_nothing(self):
        """What the Overview calls: the first view after a change pays, the rest do not."""
        self._register("test.ensure")
        self.assertTrue(ensure_fresh("test.ensure", self.experiment.id))
        self.assertTrue(ensure_fresh("test.ensure", self.experiment.id))

        self.assertEqual(1, len(self.calls))

    # ---- scope ----------------------------------------------------------------------
    def test_a_site_scoped_rebuild_takes_no_experiment(self):
        self._register("test.site", fn=lambda: self.calls.append(("test.site",)),
                       scope=SITE_SCOPE)
        run_rebuilds(self.experiment.id, only=["test.site"])

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
        """The behavior change from `run_post_experiment_hooks`, which was a bare loop: one
        plugin raising aborted the rest and 500'd an import whose mutations were already
        committed."""
        self._register("test.explodes", fn=self._explode, priority=PRIORITY_SETTINGS)
        self._register("test.survives")

        results = run_rebuilds(self.experiment.id,
                               only=["test.explodes", "test.survives"])

        self.assertEqual({"test.explodes": False, "test.survives": True}, results)
        self.assertEqual([("test.survives", self.experiment.id)], self.calls)

    def test_a_failure_leaves_the_data_stale(self):
        """Recoverable, and recorded: the next reader tries again and `--list` shows why."""
        self._register("test.fails", fn=self._explode)
        run_rebuilds(self.experiment.id, only=["test.fails"], force=True)

        self.assertTrue(is_stale("test.fails", self.experiment.id))
        state = DerivedDataState.objects.get(name="test.fails")
        self.assertIn("the rebuild went wrong", state.last_error)

    def test_a_failure_does_not_propagate_out_of_ensure_fresh(self):
        """A page whose derived data will not rebuild should render what it has, not 500."""
        self._register("test.page_fails", fn=self._explode)
        self.assertFalse(ensure_fresh("test.page_fails", self.experiment.id))

    def test_a_successful_rebuild_clears_a_previous_error(self):
        outcomes = iter([self._explode, lambda experiment_id: None])
        self._register("test.recovers", fn=lambda experiment_id: next(outcomes)(experiment_id))
        run_rebuilds(self.experiment.id, only=["test.recovers"], force=True)
        run_rebuilds(self.experiment.id, only=["test.recovers"], force=True)

        state = DerivedDataState.objects.get(name="test.recovers")
        self.assertEqual("", state.last_error)
        self.assertIsNone(state.stale_since)

    def test_being_invalidated_mid_rebuild_leaves_it_stale(self):
        """The rebuild that just finished never saw the second change, so clearing the flag
        would lose it silently."""
        experiment_id = self.experiment.id

        def invalidate_myself(_experiment_id):
            request_rebuild(experiment_id, only=["test.racy"], reason="changed again")

        self._register("test.racy", fn=invalidate_myself)
        run_rebuilds(experiment_id, only=["test.racy"], force=True)

        self.assertTrue(is_stale("test.racy", experiment_id))

    # ---- the plugin-facing alias ----------------------------------------------------
    def test_register_post_experiment_hook_still_works(self):
        """mutint-fixation and mutint-converge call this and were not edited."""
        from mutint_common.plugin_registry import (
            register_post_experiment_hook, run_post_experiment_hooks,
        )

        seen = []
        name = register_post_experiment_hook(lambda exp_id: seen.append(exp_id))
        self.addCleanup(unregister_rebuilder, name)

        self.assertIsNotNone(get_rebuilder(name))
        run_post_experiment_hooks(self.experiment.id)
        self.assertEqual([self.experiment.id], seen)

    def test_two_anonymous_hooks_from_one_module_both_register(self):
        """The old API was a list and allowed it; deriving a name from the module must not
        turn that into an error."""
        from mutint_common.plugin_registry import register_post_experiment_hook

        first = register_post_experiment_hook(lambda exp_id: None)
        self.addCleanup(unregister_rebuilder, first)
        second = register_post_experiment_hook(lambda exp_id: None)
        self.addCleanup(unregister_rebuilder, second)

        self.assertNotEqual(first, second)
        self.assertIsNotNone(get_rebuilder(second))


class RebuildCommandTestCase(TestCase):
    """The `./mutint rebuild` command.

    It needs a rebuild with an effect it can see, and registers its own. It watched `overview`
    until that stopped storing anything, then `experiment_filter` until the shared filter row it
    defaulted stopped existing -- and core has no experiment-scoped rebuilder left to borrow at
    all: fixation and convergence compute on read, the needle plot and the Overview counts store
    nothing, and what remains is the dashboard's two site-scoped totals. Registering one is also
    the honest shape, since the command is what is under test rather than whichever rebuild
    happened to be lying around.
    """

    def setUp(self):
        from mutint_common.rebuild_registry import register_rebuilder, unregister_rebuilder

        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        created = self.client.post(
            "/project/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = Experiment.objects.get(pk=created["experiment_id"])

        self.rebuilt = []
        register_rebuilder("test.watched",
                           lambda experiment_id: self.rebuilt.append(experiment_id))
        self.addCleanup(unregister_rebuilder, "test.watched")

    def _run(self, *args, **options):
        out = StringIO()
        call_command("rebuild", *args, stdout=out, stderr=out, **options)
        return out.getvalue()

    def test_list_names_what_is_registered(self):
        output = self._run("--list")
        for name in ("sample_counts", "mutation_counts"):
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
            self._run(str(self.experiment.id), "--all")

    def _rebuilt(self, experiment):
        return experiment.id in self.rebuilt

    def test_it_rebuilds_the_named_experiment(self):
        self._run(str(self.experiment.id), only=["test.watched"])

        self.assertTrue(self._rebuilt(self.experiment))

    def test_all_rebuilds_every_live_experiment(self):
        second = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        other = Experiment.objects.get(pk=second["experiment_id"])

        self._run("--all", only=["test.watched"])

        self.assertTrue(self._rebuilt(self.experiment))
        self.assertTrue(self._rebuilt(other))

    def test_all_leaves_a_soft_deleted_experiment_alone(self):
        """Rebuilding one is work whose result nobody can see."""
        from django.utils import timezone

        Experiment.objects.filter(pk=self.experiment.pk).update(
            deleted_at=timezone.now())
        self._run("--all", only=["test.watched"])

        self.assertFalse(self._rebuilt(self.experiment))

    def test_a_second_run_reports_everything_current(self):
        self._run(str(self.experiment.id), only=["test.watched"])
        self.assertIn("0 rebuilt",
                      self._run(str(self.experiment.id), only=["test.watched"]))


class RegistrationTestCase(TestCase):
    """What registering hands back.

    This was `DeclaredInputsTestCase`, which pinned `inputs=` and `changed=` -- a rebuild saying
    what it read so that a caller could mark only the derived data depending on it. Its whole
    purpose was that a frequency-cutoff edit should not invalidate data that never reads through
    the filter. Nothing can edit a filter for anyone but themselves now, so there is no such edit
    and the mechanism went with it.
    """

    def setUp(self):
        self.calls = []


    def test_registering_hands_back_the_name(self):
        """A plugin stores it and asks `is_stale` for it later, rather than writing out a
        name the registry derives."""
        self.assertEqual("test.returned",
                         register_rebuilder("test.returned", lambda _: None))
        self.addCleanup(unregister_rebuilder, "test.returned")


"""The migration graph, checked without running it.

Two rules, and they fail in opposite directions.

**Models and migrations must agree** -- `makemigrations --check` is the whole of it. The
failure it catches is a release-time regenerate (see `docs/contributing/releasing.md`) that
silently dropped a field: the migrations still apply, the tests still pass against a database
built from the models, and only a deployment migrating an old database finds out.

**A component must not name another component's migration by filename.** Each component is its
own git repository and may renumber, squash or collapse its history at any time; a dependency
written as `('mutint_experiment', '0002_initial')` is a promise the other repository never
made. Django's `__first__` sentinel says what a foreign key actually needs -- that the table
exists -- and survives any renumbering.

That second rule is the one nothing else can catch. `makemigrations --check` is happy with a
concrete name that resolves *today*, and `./mutint check` passes even when the graph cannot be
loaded at all -- only `migrate` or `test` finds that, and only after the name has already gone
stale. So the check has to be structural, and it has to run here.

**It is nearly vacuous standalone and load-bearing assembled.** mutint-core is one component,
so with no plugins installed there is no cross-component edge to inspect. `OffenderRuleTestCase`
pins the rule itself against synthetic input so the logic cannot rot where there is nothing
real to apply it to.
"""

from django.core.management import call_command
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

from mutint_common.about_registry import component_dir, first_party_app_configs

# Django's own loose references: `__first__` is the first migration of an app and `__latest__`
# the last, neither of which is a filename anyone can invalidate.
SENTINELS = ("__first__", "__latest__")


def cross_component_offenders(disk_migrations, component_of):
    """Dependencies that name another component's migration by filename.

    `disk_migrations` maps (app_label, name) -> Migration, as `MigrationLoader` builds it.
    `component_of` maps an app label to the component directory it came from; an app missing
    from it is third-party and is neither checked nor counted as a target.

    Returns a sorted list of "app.migration -> other_app.other_migration" strings.
    """
    offenders = []
    for (app_label, name), migration in disk_migrations.items():
        mine = component_of.get(app_label)
        if mine is None:
            continue
        references = list(migration.dependencies) + list(getattr(migration, "run_before", []))
        for dep_label, dep_name in references:
            # swappable_dependency() yields ('__setting__', 'AUTH_USER_MODEL'), which names
            # no app and is skipped by the lookup below.
            theirs = component_of.get(dep_label)
            if theirs is None or theirs == mine:
                continue
            if dep_name in SENTINELS:
                continue
            offenders.append("%s.%s -> %s.%s" % (app_label, name, dep_label, dep_name))
    return sorted(offenders)


class NoModelDriftTestCase(TestCase):
    def test_makemigrations_has_nothing_left_to_do(self):
        try:
            call_command("makemigrations", "--check", "--dry-run", verbosity=0)
        except SystemExit:
            self.fail(
                "Models and migrations disagree: `./mutint makemigrations` would write a file. "
                "Generate it and commit it alongside the model change."
            )


class CrossComponentDependencyTestCase(SimpleTestCase):
    def test_no_component_names_another_components_migration(self):
        component_of = {app.label: component_dir(app) for app in first_party_app_configs()}
        loader = MigrationLoader(None, ignore_no_migrations=True)
        offenders = cross_component_offenders(loader.disk_migrations, component_of)
        self.assertEqual(
            [], offenders,
            "These dependencies name another component's migration by filename, which that "
            "component is free to rename:\n  %s\nUse ('<app>', '__first__') instead -- see "
            "docs/contributing/releasing.md. Note that `makemigrations` reverts the sentinel "
            "silently whenever the migration is regenerated." % "\n  ".join(offenders))


class OffenderRuleTestCase(SimpleTestCase):
    """The rule itself, so it still means something with no plugins installed."""

    COMPONENTS = {"core_app": "/src/core", "other_core_app": "/src/core", "plugin": "/src/plugin"}

    def _migration(self, dependencies=(), run_before=()):
        return type("M", (), {"dependencies": list(dependencies),
                              "run_before": list(run_before)})()

    def test_a_filename_across_components_is_an_offender(self):
        disk = {("plugin", "0001_initial"):
                self._migration([("core_app", "0002_initial")])}
        self.assertEqual(["plugin.0001_initial -> core_app.0002_initial"],
                         cross_component_offenders(disk, self.COMPONENTS))

    def test_a_sentinel_across_components_is_fine(self):
        for sentinel in SENTINELS:
            disk = {("plugin", "0001_initial"): self._migration([("core_app", sentinel)])}
            self.assertEqual([], cross_component_offenders(disk, self.COMPONENTS), sentinel)

    def test_a_filename_within_one_component_is_fine(self):
        # Core's own migrations name each other constantly, and may: one commit moves both.
        disk = {("core_app", "0002_initial"):
                self._migration([("other_core_app", "0001_initial")])}
        self.assertEqual([], cross_component_offenders(disk, self.COMPONENTS))

    def test_third_party_apps_are_neither_checked_nor_targets(self):
        disk = {("plugin", "0001_initial"): self._migration([("auth", "0012_alter_user")]),
                ("auth", "0012_alter_user"): self._migration([("core_app", "0001_initial")])}
        self.assertEqual([], cross_component_offenders(disk, self.COMPONENTS))

    def test_swappable_dependencies_are_ignored(self):
        disk = {("plugin", "0001_initial"):
                self._migration([("__setting__", "AUTH_USER_MODEL")])}
        self.assertEqual([], cross_component_offenders(disk, self.COMPONENTS))

    def test_run_before_is_checked_too(self):
        disk = {("plugin", "0001_initial"):
                self._migration(run_before=[("core_app", "0002_initial")])}
        self.assertEqual(["plugin.0001_initial -> core_app.0002_initial"],
                         cross_component_offenders(disk, self.COMPONENTS))

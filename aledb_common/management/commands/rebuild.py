"""Recompute derived data -- core's and every plugin's -- from the command line.

    ./aledb rebuild --list          what is registered, and what is currently stale
    ./aledb rebuild 4               everything stale for experiment 4
    ./aledb rebuild 4 --force       everything for experiment 4, stale or not
    ./aledb rebuild --all           every live experiment, plus the site-wide totals
    ./aledb rebuild --all --only overview --only aledb_fixation

Derived data goes stale for reasons no upload knows about -- a filter edit, a plugin
installed after the data was imported, a rebuild that failed and was recorded rather than
raised. This is how it catches up in bulk. A page reading one experiment's data rebuilds it
on its own (`ensure_fresh`), so this is for the cases where waiting for a reader is the wrong
answer: after an upgrade, after restoring a database, or when `--list` shows an error.
"""

from django.core.management import BaseCommand, CommandError

from aledb_common.models import DerivedDataState
from aledb_common.rebuild_registry import (
    EXPERIMENT_SCOPE, SITE_SCOPE, get_rebuilders, run_rebuilds,
)


class Command(BaseCommand):

    help = "Recompute derived data for an experiment, or for every experiment."

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", nargs="?", type=int, default=None,
                            help="AleExperiment primary key; omit for --all or --list")
        parser.add_argument("--all", action="store_true", dest="rebuild_all",
                            help="every live experiment, then the site-wide totals")
        parser.add_argument("--only", action="append", dest="only", metavar="NAME",
                            help="restrict to one registered rebuild; repeatable")
        parser.add_argument("--force", action="store_true",
                            help="rebuild even what is already current")
        parser.add_argument("--list", action="store_true", dest="show_list",
                            help="show what is registered and what is stale, and do nothing")

    def handle(self, *args, **options):
        from aledb_experiment.models import AleExperiment, live

        only = options.get("only")
        if only:
            known = {r["name"] for r in get_rebuilders()}
            unknown = [name for name in only if name not in known]
            if unknown:
                # Unlike the `only=` argument in code, which skips a plugin that is not
                # installed, a name typed at the shell is worth refusing: silently doing
                # nothing is indistinguishable from having nothing to do.
                raise CommandError(
                    "no such rebuild: %s (registered: %s)"
                    % (", ".join(unknown), ", ".join(sorted(known)) or "none"))

        if options.get("show_list"):
            self._show_list()
            return

        experiment_id = options.get("experiment_id")
        rebuild_all = options.get("rebuild_all")
        if experiment_id is None and not rebuild_all:
            raise CommandError("name an experiment, or pass --all (or --list)")
        if experiment_id is not None and rebuild_all:
            raise CommandError("--all rebuilds every experiment; do not also name one")

        if rebuild_all:
            # live(), not objects.all(): rebuilding a soft-deleted experiment is work whose
            # result nobody can see.
            experiments = list(live(AleExperiment.objects.all()))
        else:
            try:
                experiments = [AleExperiment.objects.get(ale_id=experiment_id)]
            except AleExperiment.DoesNotExist:
                raise CommandError("no such experiment: %s" % experiment_id)

        force = options.get("force")
        totals = {"rebuilt": 0, "current": 0, "failed": 0}
        for experiment in experiments:
            results = run_rebuilds(experiment.ale_id, only=only, force=force)
            self._report(str(experiment.ale_id), experiment.name, results, totals,
                         len(get_rebuilders(scope=EXPERIMENT_SCOPE, only=only)))

        if get_rebuilders(scope=SITE_SCOPE, only=only):
            # Site-wide totals last, and once: they count rows across every experiment, so
            # running them per experiment would recompute the same numbers N times.
            results = run_rebuilds(None, only=only, force=force)
            self._report("site", "installation totals", results, totals,
                         len(get_rebuilders(scope=SITE_SCOPE, only=only)))

        self.stdout.write("")
        self.stdout.write("%d rebuilt, %d already current, %d failed"
                          % (totals["rebuilt"], totals["current"], totals["failed"]))
        if totals["failed"]:
            self.stdout.write(self.style.WARNING(
                "failures are recorded, not raised -- see ./aledb rebuild --list"))

    def _report(self, where, name, results, totals, considered):
        rebuilt = sorted(n for n, ok in results.items() if ok)
        failed = sorted(n for n, ok in results.items() if not ok)
        totals["rebuilt"] += len(rebuilt)
        totals["failed"] += len(failed)
        totals["current"] += max(considered - len(results), 0)

        if not rebuilt and not failed:
            return
        self.stdout.write("%s (%s):" % (where, name))
        if rebuilt:
            self.stdout.write("  rebuilt  %s" % ", ".join(rebuilt))
        if failed:
            self.stdout.write(self.style.ERROR("  failed   %s" % ", ".join(failed)))

    def _show_list(self):
        rebuilders = get_rebuilders()
        if not rebuilders:
            self.stdout.write("nothing registered")
            return

        stale = {}
        errors = {}
        for state in DerivedDataState.objects.all():
            if state.stale_since is not None:
                stale[state.name] = stale.get(state.name, 0) + 1
            if state.last_error:
                errors.setdefault(state.name, state.last_error)

        width = max(len(r["name"]) for r in rebuilders)
        self.stdout.write("%s  %-10s  %s" % ("NAME".ljust(width), "SCOPE", "LABEL"))
        for rebuilder in rebuilders:
            name = rebuilder["name"]
            line = "%s  %-10s  %s" % (name.ljust(width), rebuilder["scope"],
                                      rebuilder["label"])
            count = stale.get(name)
            if count:
                line += "  [%d stale]" % count
            self.stdout.write(line)
            if name in errors:
                self.stdout.write(self.style.ERROR("  last error: %s" % errors[name]))

        # A rebuilder with no row anywhere has never run, which reads as "nothing stale"
        # above only because there is nothing to have marked. Say so rather than imply
        # everything is current.
        never = [r["name"] for r in rebuilders
                 if not DerivedDataState.objects.filter(name=r["name"]).exists()]
        if never:
            self.stdout.write("")
            self.stdout.write("never built (so stale everywhere): %s" % ", ".join(never))

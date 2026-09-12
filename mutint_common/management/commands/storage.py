"""How much disk each experiment's stored data takes, and clearing a kind of it.

    ./mutint storage --list                   every live experiment, per kind, with totals
    ./mutint storage 4                        one experiment's kinds
    ./mutint storage --clear alignments 4     remove that kind's files for experiment 4

The shell counterpart of the Storage panel on an experiment's Overview and the Stored Data
panel on the dashboard. It exists because "disk full" is met at a shell, beside
`purge_deleted` and `coverage`, which are shell-only for the same reason.

**`--clear` does not honour the experiment lock.** The lock guards the web, and management
commands write to a locked experiment the way `./mutint import` does; the web endpoint is
where a lock refuses. The command says so in its own output.

Sizes are the stored rows, measured here when stale so the table is current; a `(stale)`
mark means the measurement failed and `./mutint rebuild --list` says why.
"""

from django.core.management import BaseCommand, CommandError
from django.template.defaultfilters import filesizeformat

from mutint_common.storage_registry import (
    NotClearable, UnknownStorageKind, clear_kind, ensure_measured, get_storage_kinds,
    is_clearable, stale_experiment_ids, usage_for,
)


class Command(BaseCommand):

    help = "Report the stored data each experiment holds, or clear one kind of it."

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", nargs="?", type=int, default=None,
                            help="Experiment primary key; omit for --list")
        parser.add_argument("--list", action="store_true", dest="show_list",
                            help="every live experiment, one line each, with totals")
        parser.add_argument("--clear", metavar="KIND", dest="clear",
                            help="remove this kind of stored data for the named experiment "
                                 "(the web's experiment lock is not consulted here)")

    def handle(self, *args, **options):
        from mutint_experiment.models import Experiment, live

        kinds = get_storage_kinds()
        if not kinds:
            raise CommandError("no kinds of stored data are registered")

        experiment_id = options.get("experiment_id")
        clear = options.get("clear")

        if options.get("show_list"):
            if experiment_id is not None or clear:
                raise CommandError("--list takes no experiment and no --clear")
            self._list(list(live(Experiment.objects.all()).order_by("id")), kinds)
            return

        if experiment_id is None:
            raise CommandError("name an experiment, or pass --list")
        try:
            experiment = Experiment.objects.get(pk=experiment_id)
        except Experiment.DoesNotExist:
            raise CommandError("no such experiment: %s" % experiment_id)

        if clear:
            self._clear(experiment, clear, kinds)
            return

        ensure_measured(experiment.id)
        self._one(experiment)

    def _clear(self, experiment, key, kinds):
        known = [k["key"] for k in kinds]
        if key not in known:
            # A typo at the shell is worth refusing: silently doing nothing is
            # indistinguishable from having nothing to do.
            raise CommandError("no such kind of stored data: %s (registered: %s)"
                               % (key, ", ".join(known)))
        if not is_clearable(key):
            raise CommandError("%s is measured but cannot be cleared" % key)
        if experiment.is_locked:
            self.stdout.write(self.style.WARNING(
                "experiment %s is locked; the lock guards the web and this command "
                "proceeds anyway" % experiment.id))
        try:
            freed = clear_kind(experiment, key)
        except (UnknownStorageKind, NotClearable) as error:  # pragma: no cover
            raise CommandError(str(error))
        self.stdout.write("cleared %s for experiment %s (%s): freed %s"
                          % (key, experiment.id, experiment.name, filesizeformat(freed)))

    def _one(self, experiment):
        self.stdout.write("experiment %s (%s):" % (experiment.id, experiment.name))
        total = 0
        for row in usage_for(experiment):
            total += row["bytes"]
            note = "" if row["clearable"] else "  (measured only)"
            self.stdout.write("  %-28s %12s%s"
                              % (row["label"], filesizeformat(row["bytes"]), note))
        self.stdout.write("  %-28s %12s" % ("total", filesizeformat(total)))

    def _list(self, experiments, kinds):
        from mutint_common.storage_registry import bytes_by_kind

        for experiment in experiments:
            ensure_measured(experiment.id)
        stale = stale_experiment_ids(e.id for e in experiments)

        labels = [k["label"] for k in kinds]
        header = "%-6s %-30s %12s" % ("ID", "EXPERIMENT", "TOTAL")
        header += "".join("  %18s" % label[:18] for label in labels)
        self.stdout.write(header)
        for experiment in experiments:
            rows = usage_for(experiment)
            total = sum(r["bytes"] for r in rows)
            line = "%-6s %-30s %12s" % (experiment.id, experiment.name[:30],
                                        filesizeformat(total))
            line += "".join("  %18s" % filesizeformat(r["bytes"]) for r in rows)
            if experiment.id in stale:
                line += "  (stale)"
            self.stdout.write(line)

        self.stdout.write("")
        from mutint_experiment.models import Experiment, live
        for key, label, size in bytes_by_kind(live(Experiment.objects.all())):
            self.stdout.write("%-40s %12s" % (label, filesizeformat(size)))
        self.stdout.write("%-40s %12s" % ("total in live experiments", filesizeformat(
            sum(s for _, _, s in bytes_by_kind(live(Experiment.objects.all()))))))
        self._site_lines()

    def _site_lines(self):
        from django.db.models import Q, Sum

        from mutint_common.models import StorageUsage
        from mutint_common.rebuild_registry import ensure_fresh
        from mutint_common.storage_registry import UNATTRIBUTED_REBUILD

        awaiting = (StorageUsage.objects
                    .filter(Q(experiment__deleted_at__isnull=False)
                            | Q(experiment__project__deleted_at__isnull=False))
                    .aggregate(total=Sum("bytes"))["total"]) or 0
        self.stdout.write("%-40s %12s" % ("awaiting purge (deleted experiments)",
                                          filesizeformat(awaiting)))
        ensure_fresh(UNATTRIBUTED_REBUILD)
        try:
            from mutint_dashboard.models import InstallationCounts
            from mutint_dashboard.util import counts
            unattributed = counts(InstallationCounts.STORAGE_UNATTRIBUTED).get("total", 0)
        except Exception:  # noqa: BLE001 -- the dashboard app is what registers that row
            unattributed = 0
        self.stdout.write("%-40s %12s" % ("unattributed (staging, orphans)",
                                          filesizeformat(unattributed)))
        from mutint_common.storage_registry import database_bytes
        self.stdout.write("%-40s %12s" % ("database", filesizeformat(database_bytes())))

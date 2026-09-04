from django.core.management import BaseCommand

from aledb_common.rebuild_registry import SITE_SCOPE, run_rebuilds


class Command(BaseCommand):

    help = "Recompute the installation-wide counts shown on the dashboard."

    def handle(self, *args, **options):
        # Through the registry, not by calling the two rebuild functions directly, which is
        # what this did. Both spellings write the same numbers; only this one *settles* the
        # staleness rows, so the next reader of /dashboard does not recompute what was just
        # written. `delete_experiments` had the identical fault and took the identical fix.
        #
        # `scope=SITE_SCOPE` rather than naming `sample_counts` and `mutation_counts`: the
        # installation-wide totals are exactly the site-scoped rebuilders, and a list of names
        # here would be a second copy of what `aledb_dashboard.apps` already declares.
        results = run_rebuilds(scope=SITE_SCOPE, force=True)
        for name, succeeded in sorted(results.items()):
            self.stdout.write("%s: %s" % (name, "ok" if succeeded else "failed"))

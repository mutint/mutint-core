"""Remove queue rows that nothing will ever finish.

`django_tasks_db`'s own `prune_db_task_results` clears *finished* rows after a fortnight. It
cannot touch a row no worker ever claimed, so a READY row left behind by an installation with
no worker running is immortal -- which is how four coverage tasks came to be sitting in this
suite's two dev databases with nothing in existence that would ever remove them.

Cron's job, like `./aledb reap_uploads` and `./aledb purge_deleted`; nothing runs it for you.

    ./aledb reap_jobs                  # older than 14 days
    ./aledb reap_jobs --older-than 30
    ./aledb reap_jobs --dry-run        # say what would go, remove nothing
"""

from django.core.management.base import BaseCommand

from aledb_jobs import jobs


class Command(BaseCommand):
    help = "Delete queued or running task rows abandoned longer than --older-than days."

    def add_arguments(self, parser):
        parser.add_argument("--older-than", type=int, default=jobs.DEFAULT_REAP_DAYS,
                            dest="older_than",
                            help="Age in days beyond which a row is abandoned "
                                 "(default: %(default)s)")
        parser.add_argument("-n", "--dry-run", action="store_true",
                            help="Report what would be removed without removing it")

    def handle(self, *args, **options):
        rows, orphans = jobs.reap_stranded(older_than_days=options["older_than"],
                                           dry_run=options["dry_run"])
        verb = "Would reap" if options["dry_run"] else "Reaped"
        self.stdout.write("%s %d stranded queue row(s) and %d job row(s)."
                          % (verb, rows, orphans))

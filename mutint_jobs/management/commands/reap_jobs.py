"""Remove queue rows that nothing will ever finish.

`django_tasks_db`'s own `prune_db_task_results` clears *finished* rows after a fortnight. It
cannot touch a row no worker ever claimed, so a READY row left behind by an installation with
no worker running is immortal -- which is how four coverage tasks came to be sitting in this
suite's two dev databases with nothing in existence that would ever remove them.

Cron's job, like `./mutint reap_uploads` and `./mutint purge_deleted`; nothing runs it for you.

    ./mutint reap_jobs                  # older than 14 days
    ./mutint reap_jobs --older-than 30
    ./mutint reap_jobs --dry-run        # say what would go, remove nothing

It also sweeps job **logs** that belong to no job row -- see `mutint_jobs.logs.orphans`.
"""

from django.core.management.base import BaseCommand

from mutint_jobs import jobs, logs


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

        # Logs whose `Job` never existed. A row's own log goes with it through the
        # `post_delete` receiver, but work enqueued with a bare `task.enqueue` leaves one
        # nothing else can reach -- and this is the command that exists for exactly that
        # class of litter.
        stray = logs.orphans()
        if stray:
            if not options["dry_run"]:
                for task_result_id in stray:
                    logs.discard(task_result_id)
            self.stdout.write("%s %d job log(s) belonging to no job row."
                              % (verb, len(stray)))

"""Permanently remove projects and experiments that were soft-deleted long enough ago.

Deletion in the UI only flags a row. This is the second half: run it from cron with a
retention window so flagged rows age out, or by hand with --dry-run first.

Purging an experiment reuses `delete_experiments`, which already handles the two things
a plain `.delete()` misses -- the sweep of mutations left orphaned
once their calls go. It also removes the experiment's files from the managed store,
which nothing else in the codebase does.

Projects are purged after their experiments: `Experiment.project` is DO_NOTHING, so
deleting a project first would orphan its experiments into permanent invisibility.
"""

import shutil

from django.core.management.base import BaseCommand
from django.utils import timezone

from aledb_common import store
from aledb_experiment.models import Experiment, Project
from aledb_experiment import paths

DEFAULT_RETENTION_DAYS = 30


class Command(BaseCommand):
    help = "Permanently delete projects and experiments soft-deleted before the cutoff."

    def add_arguments(self, parser):
        parser.add_argument("--older-than", type=int, default=DEFAULT_RETENTION_DAYS,
                            metavar="DAYS",
                            help="Purge rows deleted more than DAYS ago (default %d)."
                                 % DEFAULT_RETENTION_DAYS)
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would be purged and change nothing.")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timezone.timedelta(days=options["older_than"])
        dry_run = options["dry_run"]

        experiments = list(Experiment.objects.filter(
            deleted_at__isnull=False, deleted_at__lt=cutoff))
        projects = list(Project.objects.filter(
            deleted_at__isnull=False, deleted_at__lt=cutoff))

        # An experiment inside a project being purged goes with it, even if the experiment
        # itself was never flagged.
        for project in projects:
            for experiment in Experiment.objects.filter(project=project):
                if experiment not in experiments:
                    experiments.append(experiment)

        for experiment in experiments:
            self.stdout.write("%s experiment #%s %s"
                              % ("Would purge" if dry_run else "Purging",
                                 experiment.id, experiment.name))
            if not dry_run:
                self._purge_experiment(experiment)

        for project in projects:
            self.stdout.write("%s project #%s %s"
                              % ("Would purge" if dry_run else "Purging",
                                 project.id, project.name))
            if not dry_run:
                project.delete()

        self.stdout.write("%s %d experiment(s) and %d project(s)."
                          % ("Would purge" if dry_run else "Purged",
                             len(experiments), len(projects)))

    def _purge_experiment(self, experiment):
        from aledb_import.ale_experiment import delete_experiments

        experiment_id = experiment.id
        sample_dirs = [store.sample_dir(reseq.id)
                       for reseq in self._samples(experiment)]

        delete_experiments([experiment_id])

        # Files are keyed by database id, so they must be collected before the rows go.
        shutil.rmtree(store.experiment_reference_dir(experiment_id), ignore_errors=True)
        for path in sample_dirs:
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _samples(experiment):
        from aledb_sample.models import Sample

        # Nothing below Population carries an experiment id, so this is the four-hop traversal.
        return Sample.objects.filter(
            **{paths.to_experiment(): experiment})

"""Rename an experiment's sequences to match a reference file, from the shell.

    ./mutint rename_contigs 4 --ref REL606.gbk          # show the plan, change nothing
    ./mutint rename_contigs 4 --ref REL606.gbk --yes    # do it
    ./mutint rename_contigs 4 --repair                  # recompute identity from the store

The same operation the Add Data page offers, and the same gate: without `--yes` it prints
what would change and exits, because renaming rewrites every mutation in the experiment.

`--repair` is the way out of the one state that blocks a rename: a reference recorded before
per-sequence hashes existed, or backfilled while its files were missing from the store. It
recomputes identity from the stored FASTA and changes nothing else.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from mutint_experiment.models import Experiment
from mutint_import import reference as reference_io
from mutint_import import reference_rename, reference_store
from mutint_sample.models import ReferenceSequences, Mutation


class Command(BaseCommand):
    help = "Rename an experiment's contigs to match a reference file."

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", type=int)
        parser.add_argument("--ref", dest="reference_path", default=None,
                            help="GenBank, GFF3 or FASTA carrying the intended names")
        parser.add_argument("--yes", action="store_true",
                            help="perform the rename instead of describing it")
        parser.add_argument("--repair", action="store_true",
                            help="recompute sequence identity from the stored FASTA")

    def handle(self, *args, **options):
        experiment = self._experiment(options["experiment_id"])
        reference = ReferenceSequences.objects.filter(experiment=experiment).first()
        if reference is None:
            raise CommandError("Experiment %s has no reference genome."
                               % experiment.id)

        if options["repair"]:
            return self._repair(reference)

        if not options["reference_path"]:
            raise CommandError("Pass --ref <file>, or --repair.")
        path = options["reference_path"]
        if not os.path.isfile(path):
            raise CommandError("No such file: %s" % path)

        gff3_text, sequences = reference_io.normalize_reference(
            path, os.path.basename(path))

        try:
            plan = reference_rename.plan_rename(reference, sequences)
        except reference_rename.RenameError as error:
            raise CommandError(str(error))

        if not plan:
            self.stdout.write("Nothing to do: the names already match.")
            return

        self._describe(experiment, plan)
        if not options["yes"]:
            self.stdout.write("\nNothing was changed. Re-run with --yes to apply.")
            return

        reference_store.establish_or_check(
            experiment, gff3_text, sequences, update_annotation=True, allow_rename=True)
        self.stdout.write("Renamed.")

    def _experiment(self, experiment_id):
        try:
            return Experiment.objects.get(pk=experiment_id)
        except Experiment.DoesNotExist:
            raise CommandError("No experiment with id %s." % experiment_id)

    def _describe(self, experiment, plan):
        self.stdout.write("Renaming in %r:" % experiment.name)
        for old, new in plan.pairs:
            self.stdout.write("  %-30s -> %s" % (old, new))
        for name in plan.unchanged:
            self.stdout.write("  %-30s    (unchanged)" % name)
        affected = Mutation.objects.filter(
            experiment=experiment, seq_id__in=list(plan.mapping)).count()
        self.stdout.write("\n%d mutation(s) would be rewritten." % affected)
        self.stdout.write(
            "Stored alignments keep their current names and go on working; igv is given an "
            "alias table.\nRe-importing these samples from their original breseq folders "
            "will be refused afterwards.")

    def _repair(self, reference):
        before = reference.sequence_sha256
        reference_store._ensure_sequence_identity(reference)
        reference.refresh_from_db()
        if not reference.sequence_sha256:
            raise CommandError(
                "Could not read the stored FASTA for experiment %s, so its identity cannot "
                "be recomputed. Re-establish the reference instead."
                % reference.experiment_id)
        self.stdout.write(
            "Identity %s for experiment %s: %s"
            % ("recomputed" if before else "computed",
               reference.experiment_id, reference.sequence_sha256[:12]))

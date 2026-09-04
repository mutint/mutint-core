"""Re-annotate an experiment's mutations against its reference genome.

The reason to run this is that a better reference arrived. Annotation happens at
import, from whatever reference the experiment had then; when a new one is
established -- a real annotation, replacing the bare FASTA someone started with,
or simply a newer build -- every mutation's gene, codon and amino-acid fields are
stale until this recomputes them.

    ./aledb reannotate 4                      # against the reference it has
    ./aledb reannotate 4 --ref REL606.gbk     # attach a new one, then re-annotate
    ./aledb reannotate 4 --ref new.gbk -n     # report what would change

Mutations carry the .gd record they were imported from
(``Mutation.genome_diff``), so
this needs nothing on disk but the reference itself.
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from aledb_experiment.models import Experiment
from aledb_import import annotation, reference as reference_io, reference_store
from aledb_import.gd_import import run_post_processing
from aledb_sample.models import ReferenceSequences, Mutation


class Command(BaseCommand):

    help = ("Re-annotate an experiment's mutations against its reference genome. "
            "Use --ref to establish a new reference first.")

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", type=int,
                            help="Experiment primary key (ale_id)")
        parser.add_argument("--ref", dest="reference_path", default=None,
                            help="GenBank, GFF3 or FASTA to establish before re-annotating")
        parser.add_argument("--replace", action="store_true",
                            help="With --ref, accept a reference whose *sequence* differs. "
                                 "This is a different genome; mutation coordinates may no "
                                 "longer mean what they did.")
        parser.add_argument("-n", "--dry-run", action="store_true",
                            help="Report what would change without writing anything")
        parser.add_argument("--skip-rebuilds", action="store_true",
                            help="Do not recompute convergence, fixation, stats and the "
                                 "dashboard afterwards")

    def handle(self, *args, **options):
        experiment = self._experiment(options["experiment_id"])
        dry_run = options["dry_run"]

        references = self._reference(experiment, options["reference_path"],
                                     options["replace"], dry_run)

        mutations = list(Mutation.objects.filter(experiment=experiment))
        if not mutations:
            self.stdout.write("Experiment %r has no mutations." % experiment.name)
            return

        annotated, changed, skipped, failed = self._reannotate(
            experiment, mutations, references, dry_run)

        self.stdout.write("  mutations:  %d" % len(mutations))
        self.stdout.write("  annotated:  %d (%d changed)" % (annotated, changed))
        if skipped:
            self.stdout.write(
                "  skipped:    %d with no stored .gd record -- these came in through "
                "the breseq-directory CLI path, which does not keep one, and can only "
                "be re-annotated by re-importing" % skipped)
        if failed:
            self.stdout.write(self.style.WARNING("  failed:     %d" % failed))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing was written."))
            return
        if not changed:
            self.stdout.write(self.style.SUCCESS("Already up to date."))
            return

        if options["skip_rebuilds"]:
            self.stdout.write(
                "Skipping rebuilds; convergence, fixation, stats and the dashboard "
                "are now stale for this experiment.")
        else:
            self.stdout.write("Recomputing derived data...")
            run_post_processing(experiment)
        self.stdout.write(self.style.SUCCESS("Done."))

    def _experiment(self, experiment_id):
        try:
            return Experiment.objects.get(pk=experiment_id)
        except Experiment.DoesNotExist:
            raise CommandError("No experiment with ale_id=%s" % experiment_id)

    def _reference(self, experiment, reference_path, replace, dry_run):
        """Establish `reference_path` if given, then load what we will annotate against."""
        annotation.clear_cache()

        if not reference_path:
            references = annotation.reference_sequences_for(experiment)
            if references is None:
                raise CommandError(
                    "Experiment %r has no stored reference to annotate against. "
                    "Pass --ref <file> to establish one." % experiment.name)
            self.stdout.write("Re-annotating %r against its stored reference."
                              % experiment.name)
            return references

        if not os.path.isfile(reference_path):
            raise CommandError("Reference file not found: %s" % reference_path)

        try:
            gff3_text, sequences = reference_io.normalize_reference(
                reference_path, os.path.basename(reference_path))
        except reference_io.ReferenceFormatError as error:
            raise CommandError(str(error))

        before = ReferenceSequences.objects.filter(
            experiment=experiment).values_list("gff3_sha256", flat=True).first()

        if dry_run:
            # Annotate against the file itself rather than establishing it, so a
            # dry run really does leave the store untouched.
            self.stdout.write("Would establish %s and re-annotate %r."
                              % (os.path.basename(reference_path), experiment.name))
            return annotation.load_reference(reference_path)

        try:
            reference_store.establish_or_check(
                experiment, gff3_text, sequences,
                replace=replace, update_annotation=True)
        except reference_store.ReferenceMismatch as error:
            raise CommandError(
                "%s\nThis is a different genome, not a re-annotation of the same one. "
                "Pass --replace if that is really what you mean." % error)

        after = ReferenceSequences.objects.get(experiment=experiment).gff3_sha256
        if before is None:
            self.stdout.write("Established %s as the reference for %r."
                              % (os.path.basename(reference_path), experiment.name))
        elif before == after:
            self.stdout.write("%s matches the stored reference; re-annotating anyway."
                              % os.path.basename(reference_path))
        else:
            self.stdout.write("Stored annotation refreshed from %s."
                              % os.path.basename(reference_path))

        annotation.clear_cache()
        references = annotation.reference_sequences_for(experiment)
        if references is None:
            raise CommandError("Reference was established but cannot be read back.")
        return references

    def _reannotate(self, experiment, mutations, references, dry_run):
        """One line, because the loop is shared with the contig-rename path.

        It lived here first; `annotation.reannotate_experiment` is the same code, moved so a
        rename re-annotates by exactly the rule this command does. Two copies would drift,
        and the failure that causes -- annotation silently not applied -- is invisible.
        """
        return annotation.reannotate_experiment(
            experiment, mutations=mutations, references=references, dry_run=dry_run,
            on_error=self.stderr.write)

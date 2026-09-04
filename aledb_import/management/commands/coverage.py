"""Derive coverage BigWigs for samples that have an alignment but no coverage track.

New imports build one automatically. This is for the samples already in the store -- the ones
imported before coverage existed, and the ones whose derivation failed because the external
tools were not installed yet.

    ./aledb coverage            # every experiment
    ./aledb coverage 4          # one experiment
    ./aledb coverage 4 -n       # say what would be built, write nothing
    ./aledb coverage 4 --force  # rebuild even where a track already exists

Nothing is regenerated implicitly: a sample that already has one is skipped unless --force.

Each built sample reports what its BAM held -- how much of it was redundantly mapped, or that
it carried no X1 tag at all. Coverage weights each alignment by 1/X1 (breseq's redundancy
tag), and nothing in the database records which rule an existing BigWig was built under, so
this output is the only way to tell a normalized track from one that predates that change.

**Every BigWig built before it is still the old, unnormalized kind.** `--force` re-derives them.
"""

from django.core.management.base import BaseCommand

from aledb_import import coverage
from aledb_seq.models import Sample
from aledb_experiment import paths


class Command(BaseCommand):
    help = "Build coverage BigWigs for stored samples that lack one."

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", type=int, nargs="?", default=None,
                            help="Experiment primary key; omit for every experiment")
        parser.add_argument("-n", "--dry-run", action="store_true",
                            help="Report what would be built without writing anything")
        parser.add_argument("--force", action="store_true",
                            help="Rebuild samples that already have a coverage track")

    def handle(self, *args, **options):
        samples = Sample.objects.filter(bam_stored=True).select_related(
            paths.to_experiment())
        if options["experiment_id"] is not None:
            samples = samples.filter(
                **{paths.to_experiment_id(): options["experiment_id"]})
        if not options["force"]:
            samples = samples.exclude(coverage_stored=True)

        samples = list(samples)
        if not samples:
            self.stdout.write("Nothing to do: every stored sample already has coverage.")
            return

        built = failed = untagged = 0
        for reseq in samples:
            name = reseq.label
            if options["dry_run"]:
                self.stdout.write("  %s  would build" % name)
                continue
            # One sample's missing tool or corrupt BAM must not stop the rest, exactly as the
            # importer isolates a bad sample from its batch.
            try:
                # The tally is the only thing that can say whether the weighting did
                # anything: nothing in the database records which rule a stored BigWig was
                # built under, so an IS element still towering over the trace could mean an
                # old file, a BAM with no X1, or a broken derivation. Printed per sample so
                # that question is answered where it is asked.
                tally = coverage.build_for(reseq)
                built += 1
                self.stdout.write("  %s  built  (%s)" % (name, tally.describe()))
                if not tally.normalized:
                    untagged += 1
            except Exception as error:  # noqa: BLE001
                failed += 1
                self.stderr.write("  %s  failed: %s" % (name, error))

        if options["dry_run"]:
            self.stdout.write("%d sample(s) would be built." % len(samples))
            return
        self.stdout.write("Built %d, failed %d." % (built, failed))

"""Record and verify the NCBI accession of a reference contig.

    ./aledb ncbi_accessions --list                     # what is recorded; touches no network
    ./aledb ncbi_accessions 4 --list                   # just this experiment's contigs
    ./aledb ncbi_accessions 4 --seq-id NC_000913 \
                              --accession NC_000913.3  # state one, and check it
    ./aledb ncbi_accessions --recheck                  # re-verify everything recorded
    ./aledb ncbi_accessions 4 --recheck                # ... for one experiment

**Nothing is inferred.** A bare run has nothing to propose, because an accession is only ever
stated by a person -- see `aledb_seq.ncbi` for why guessing from a contig name is refused.
`--recheck` therefore revisits only contigs somebody has already named.

This is where a MISMATCH surfaces for anybody not looking at the page: `--list` prints the
stored `detail`, which is the sentence saying whether the lengths differed or the bases did.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from aledb_seq import ncbi
from aledb_seq.models import ExperimentReference, NcbiSequence

#: Seconds between requests. NCBI allows 3/second unauthenticated and 10 with a key; this is
#: comfortably under the slower limit and this command is never in a hurry.
POLITE_DELAY_SECONDS = 0.4


class Command(BaseCommand):
    help = "Record and verify the NCBI accession of each reference contig."

    def add_arguments(self, parser):
        parser.add_argument("experiment_id", type=int, nargs="?", default=None,
                            help="Experiment primary key; omit for every experiment")
        parser.add_argument("--list", action="store_true", dest="list_only",
                            help="Show what is recorded and stop; makes no requests")
        parser.add_argument("--seq-id", default=None,
                            help="The contig to act on, as it is named in the reference")
        parser.add_argument("--accession", default=None,
                            help="The NCBI accession this contig is, to record and verify")
        parser.add_argument("--recheck", action="store_true",
                            help="Re-verify contigs that already have an accession recorded")

    def handle(self, *args, **options):
        contigs = self._contigs(options["experiment_id"])
        if not contigs:
            self.stdout.write("No stored reference sequences found.")
            return

        if options["accession"]:
            self._record_one(contigs, options)
        elif options["recheck"]:
            self._recheck(contigs)
        elif not options["list_only"]:
            # Neither stating nor rechecking leaves nothing to do, and silently printing the
            # listing would hide that the command did not do what was asked of it.
            self.stdout.write(self.style.WARNING(
                "Nothing to do: an accession is never guessed, so say which contig is what "
                "with --seq-id and --accession, or re-verify what is recorded with --recheck."))

        self._report(contigs)

    # -- gathering ---------------------------------------------------------------------

    def _contigs(self, experiment_id):
        """Every stored contig as `(experiment, seq_id, length, sha256)`, deduplicated later.

        Read from `ExperimentReference.seq_ids`, which already carries the per-contig digest
        the whole check compares against -- no reference file is opened here or anywhere.
        """
        references = ExperimentReference.objects.select_related("experiment")
        if experiment_id is not None:
            references = references.filter(experiment_id=experiment_id)
            if not references.exists():
                raise CommandError("No stored reference for experiment %s." % experiment_id)

        rows = []
        for reference in references:
            for entry in reference.seq_ids or []:
                if entry.get("sha256") and entry.get("length") and entry.get("id"):
                    rows.append((reference.experiment, entry["id"],
                                 entry["length"], entry["sha256"]))
        return rows

    # -- actions -----------------------------------------------------------------------

    def _record_one(self, contigs, options):
        seq_id = options["seq_id"]
        if not seq_id:
            raise CommandError("--accession needs --seq-id, so it is clear which contig it names.")

        targets = [row for row in contigs if row[1] == seq_id]
        if not targets:
            known = sorted({row[1] for row in contigs})
            raise CommandError(
                "No stored contig named %r. Known contigs: %s" % (seq_id, ", ".join(known)))

        # One digest may be reached through several experiments; check it once.
        _experiment, _name, length, sha256 = targets[0]
        self.stdout.write("Checking %s against %s ..." % (seq_id, options["accession"]))
        record = ncbi.check_and_store(sha256, length, options["accession"])
        self._say(record)

    def _recheck(self, contigs):
        by_digest = {row[3]: row for row in contigs}
        recorded = [record for record in ncbi.records_for(by_digest).values()
                    if record.accession]
        if not recorded:
            self.stdout.write("Nothing has an accession recorded yet, so there is nothing "
                              "to re-verify.")
            return

        for index, record in enumerate(recorded):
            if index:
                time.sleep(POLITE_DELAY_SECONDS)
            _experiment, name, length, sha256 = by_digest[record.sha256]
            self.stdout.write("Re-checking %s against %s ..." % (name, record.accession))
            self._say(ncbi.check_and_store(sha256, length, record.accession))

    # -- reporting ---------------------------------------------------------------------

    def _say(self, record):
        style = self.style.SUCCESS if record.is_verified else self.style.WARNING
        self.stdout.write(style("  %s: %s" % (record.status, record.detail or "-")))

    def _report(self, contigs):
        records = ncbi.records_for({row[3] for row in contigs})
        self.stdout.write("")
        self.stdout.write("%-24s %-14s %-22s %s" % ("CONTIG", "EXPERIMENT", "ACCESSION", "STATUS"))
        seen = set()
        for experiment, name, _length, sha256 in contigs:
            if (name, sha256) in seen:
                continue
            seen.add((name, sha256))
            record = records.get(sha256)
            status = record.status if record else NcbiSequence.UNCHECKED
            accession = (record.accession if record else "") or "-"
            line = "%-24s %-14s %-22s %s" % (name[:24], str(experiment.id)[:14],
                                             accession[:22], status)
            if record and not record.is_verified and record.detail:
                line += "\n%s%s" % (" " * 24, record.detail)
            self.stdout.write(line)

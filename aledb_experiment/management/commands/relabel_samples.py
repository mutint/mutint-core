"""Clear descriptions that are a coordinate in the retired format.

    ./aledb relabel_samples --dry-run      # say what it would clear
    ./aledb relabel_samples                # clear it

`Sample.description` wins over the computed coordinate wherever a sample is labeled, so a
description reading `A1 F30000 I1` pins the *old* format onto a page that writes the new one
everywhere else -- two samples side by side, one saying `A1 F30000 I1` and its neighbour
`1 / 30000 / 1-1`, for the same three numbers.

**No import has ever written one.** `gd_import` passes `""` for an A-F-I-R filename on
purpose -- filling it would relabel every table column with the filename it came from -- and
writes the filename itself only for the shapes that say something a coordinate does not
(`Ara-2_500gen_763A`). So anything this finds was typed by hand while the coordinate looked
like that, which is also why there is no way to do it automatically at migration time and no
reason to: it is rare, and it is somebody's text.

Hence `--dry-run` first and an exact anchored match: a description that merely *contains*
such a string is prose about a sample, not a stale label, and is left alone.
"""

from django.core.management.base import BaseCommand

from aledb_experiment import coordinates
from aledb_sample.models import Sample


class Command(BaseCommand):

    help = "Clear sample descriptions that are a coordinate in the retired A/F/I format."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="list what would be cleared and change nothing")

    def handle(self, *args, **options):
        stale = [sample for sample in Sample.objects.exclude(description="")
                                                    .exclude(description__isnull=True)
                 if coordinates.RETIRED_LABEL.match(sample.description.strip())]

        if not stale:
            self.stdout.write("No sample carries a description in the retired format.")
            return

        for sample in stale:
            # What it says now, and what it will say once the description is gone. Printed
            # as a pair because the point of clearing it is that the second is right.
            self.stdout.write("  %-28s -> %s" % (
                sample.description,
                coordinates.format_coordinate(sample.population_name,
                                              sample.time_point, sample.name)))

        if options["dry_run"]:
            self.stdout.write("\n%d would be cleared. Re-run without --dry-run to do it."
                              % len(stale))
            return

        Sample.objects.filter(pk__in=[sample.pk for sample in stale]).update(description="")
        self.stdout.write("\nCleared %d." % len(stale))

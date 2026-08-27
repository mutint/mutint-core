"""Mark every derived table stale, so each one is recomputed the next time it is read.

Derived data can tell when an experiment's *mutations* change -- every write path calls
`request_rebuild`. It cannot tell when the **rules** change, because nothing about the
experiment moved: the code that decides what counts did. Two commits just did exactly that.

`aledb_seq.0011` removed `frequency_gatk`, which had been ANDed into `aledb_filter`'s
exclusion and made the frequency cutoff match nothing, and the floor and ceiling were ORed so
they filter for the first time. Every table derived through `filter_observed_mutations` is
therefore holding pre-fix values, and holding them while marked *fresh* -- measured on the dev
database, the dashboard stored 74,859 observations where the filter now yields 73,857, with
`stale_since` null, so `ensure_fresh` would have left it there indefinitely.

Marking rather than rebuilding, which is the whole shape of the registry: this is one UPDATE
and runs in milliseconds, and the first view of each page pays for its own recomputation.
`./aledb rebuild --all --force` is how a deployment pays it up front instead.

Not reversible in any meaningful sense -- "un-mark data as stale" would be asserting freshness
this migration exists because nobody can assert.
"""

from django.db import migrations


def mark_stale(apps, schema_editor):
    from django.utils import timezone

    DerivedDataState = apps.get_model("aledb_common", "DerivedDataState")
    DerivedDataState.objects.update(stale_since=timezone.now())


def leave_it(apps, schema_editor):
    """A no-op rather than an error, so the migration is reversible without lying.

    Rolling back the schema does not make stale data fresh, and clearing `stale_since` would
    claim it had. A row that stays marked costs one recomputation.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_common", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(mark_stale, leave_it),
    ]

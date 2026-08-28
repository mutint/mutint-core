"""Mark the dashboard's mutation counts stale: they are counting something else now.

The same situation `0002` was written for, and the reason that one says any future change to
filtering or counting logic needs its own migration. Nothing about any experiment moved, so no
write path called `request_rebuild`; what changed is the code that decides what counts, and the
stored rows are holding pre-change values while `stale_since` says they are fresh.

Two changes, both of which move a number on `/dashboard`:

  * **The filter is no longer applied.** The dashboard is an inventory of what the installation
    holds, and an experiment's frequency cutoff or ignored-gene list is one person's view of one
    experiment. On the dev database the stored total was 73,857 where the installation holds
    74,859 -- `0002` recorded those same two numbers from the other side, when the cutoff
    started working and took the total *down*.
  * **`synonymous` and `nonsynonymous` were never written.** The branch that fills them tested
    for `snp_type_synonymous` and `snp_type_nonsynonymous`, which are not the tokens in
    `FUNCTIONAL_CHANGE_TYPE_LIST`, so both columns had sat at their default of zero since they
    were added. They are filled now, so those two rise from nothing to their real values.

Only `mutation_counts` is marked. `0002` marked everything because the filter itself had
changed and every derived table read through it; nothing here touches any other rebuild --
`sample_counts` counts AleId/Flask/Isolate rows and is untouched -- and marking one needlessly
buys a recomputation nobody asked for.
"""

from django.db import migrations

def mark_stale(apps, schema_editor):
    from django.utils import timezone

    DerivedDataState = apps.get_model("aledb_common", "DerivedDataState")
    DerivedDataState.objects.filter(name="mutation_counts").update(
        stale_since=timezone.now())


def leave_it(apps, schema_editor):
    """A no-op, as in `0002`: clearing `stale_since` would assert a freshness nobody can."""


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_common", "0002_mark_everything_stale"),
    ]

    operations = [
        migrations.RunPython(mark_stale, leave_it),
    ]

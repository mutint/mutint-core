"""Mark the dashboard's mutation counts stale: the functional-change buckets are computed from a
different column now.

The third time the rule in `0002` applies, and the resolution of the question `0003` left open.
Nothing about any experiment moved, so no write path called `request_rebuild`; what changed is the
code that decides what counts, and the stored rows hold pre-change values while `stale_since` says
they are fresh.

`0003` recorded that `synonymous` and `nonsynonymous` had never been written because the branch
filling them tested for `snp_type_synonymous`/`snp_type_nonsynonymous`, and noted that they would
*still* read zero afterwards for a second reason: the vocabulary was being matched, by substring,
against `Mutation.protein_change` -- a display string reading `I34S (ATC->AGC)` that contains
neither word. The classification reads `Mutation.snp_type` now, which is breseq's own functional
class and what the annotator has been writing all along.

The shape of the correction, over the dev database's 24,088 mutations:

    unannotated    19,982  ->   2,721
    nonsynonymous       0  ->  12,833
    synonymous          0  ->   4,883
    intergenic      3,868  ->   3,046
    nonsense            0  ->     394
    pseudogene        237  ->     210

Those are per-`Mutation`-row figures. The stored columns count observations, and distinct mutations
*among those observations*, over live experiments -- so read them as the shape of the move rather
than as expected column values. The gap is not only soft-deleted experiments: a mutation whose last
observation was deleted stays in the table until something sweeps it, and counts in the first figure
and not the second. There is one such row in the dev database, which is why the unique column totals
24,087 against 24,088 mutations.

Two parts of it are worth naming because they will look like faults:

* `intergenic` falls. `snp_type` is a SNP concept -- breseq assigns it for SNP and RA entries only
  -- so a deletion sitting between two genes is no longer counted as intergenic. It borrowed that
  bucket from `protein_change`, which is not an axis about proteins at all.
* `unannotated` stays large, at 2,721, and is now almost entirely non-SNPs for the same reason.

Only `mutation_counts` is marked. `aledb_stats` stores nothing -- the Overview computes its counts
per request, so it corrects itself on the next view with nothing to invalidate -- and
`sample_counts` counts AleId/Flask/Isolate rows and is untouched.

**The dependency on `aledb_dashboard.0003` is load-bearing, not tidiness.** The rebuild this
migration schedules writes `nonsense=`, and a deployment that migrated `aledb_common` alone would
mark the counts stale before that column existed. `ensure_fresh` cannot raise, by design, so the
symptom would not be a 500: the rebuild would fail, log a `FieldError` into `last_error`, and leave
the dashboard stale indefinitely -- the quiet failure the registry's isolation deliberately trades
for. Ordering the two removes the window.
"""

from django.db import migrations


def mark_stale(apps, schema_editor):
    from django.utils import timezone

    DerivedDataState = apps.get_model("aledb_common", "DerivedDataState")
    DerivedDataState.objects.filter(name="mutation_counts").update(
        stale_since=timezone.now())


def leave_it(apps, schema_editor):
    """A no-op, as in `0002` and `0003`: clearing `stale_since` would assert a freshness that
    this migration exists because nobody can assert."""


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_common", "0003_mark_dashboard_counts_stale"),
        # See the module docstring: the rebuild this schedules writes a column that migration
        # adds, and `ensure_fresh` would swallow the failure rather than surfacing it.
        ("aledb_dashboard", "0003_add_nonsense_counts"),
    ]

    operations = [
        migrations.RunPython(mark_stale, leave_it),
    ]

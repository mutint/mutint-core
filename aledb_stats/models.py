from django.db import models
from jsonfield import JSONField

# Create your models here.
class StaticData(models.Model):
    id = models.AutoField(primary_key=True)
    mut_needle_data = JSONField(default=dict)


class ExperimentSummary(models.Model):
    """The Overview page's mutation counts, computed once instead of on every view.

    Sibling to `StaticData` above and the same idea: the needle-plot data has been precomputed
    at import since long before this, and it is the one part of `/stats` that was ever fast.
    These four dictionaries are the rest of that page, and producing them used to mean pulling
    every ObservedMutation in the experiment -- each joined across six tables and carrying two
    JSONFields and a gene column of up to 19 000 characters -- into a Python list, to arrive at
    about sixteen integers.

    Registered as the 'overview' rebuilder, so it is warmed by an import, marked stale by a
    sample renumber or a filter change, and rebuilt by the first page view after that. See
    `aledb_common/rebuild_registry.py`.

    **Deliberately not stored here: the per-sample rows of the Sample Resequencing Stats
    table.** Those were slow for a different reason -- two queries per sample fired during
    template rendering -- and the fix for that is aggregation, not caching. Leaving them live
    means editing a sample shows up immediately with nothing to invalidate.

    The counts depend on the global and per-experiment filters as well as on the mutations,
    which is why `aledb_filter`'s two views mark this stale. A summary that ignored the filters
    would disagree with every mutation table on the site.

    `django.db.models.JSONField`, not the `jsonfield` package `StaticData` uses: the native one
    is what `aledb_seq.Mutation.annotation` and `.gd_data` use, and it is the one to write new
    code against.
    """

    ale_experiment = models.OneToOneField("aledb_experiment.AleExperiment",
                                          primary_key=True, on_delete=models.CASCADE,
                                          related_name="summary")
    # {mutation_type: count} over the distinct mutations in the filtered observed set.
    mutation_type_counts = models.JSONField(default=dict)
    # {mutation_type: count} over the filtered observed mutations themselves.
    observed_mutation_type_counts = models.JSONField(default=dict)
    # {functional_change_type: count}; a mutation counts once per type its protein_change
    # contains, so these sums may legitimately exceed the mutation counts above.
    protein_change_counts = models.JSONField(default=dict)
    observed_protein_change_counts = models.JSONField(default=dict)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "experiment summaries"

    def __str__(self):
        return "summary of experiment %s" % self.ale_experiment_id

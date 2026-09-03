"""The designated ancestor, and the subtraction that follows from it.

An ALE starts from an ancestor, and that ancestor already differs from the reference genome.
Those differences are the starting line rather than evolution: convergence should not count
them as convergent, fixation should not report them fixed in every flask because they were
there before the first one, and the needle plot should not plot them.

**One designation, one exclusion.** `AleExperiment.ancestor` names one sample. Everything
below derives from that column; there is nothing else to configure and nothing to keep in
sync. It replaced four half-built spellings of the same idea, none of which subtracted
anything: `AleId.starting_strain` (a FK no code path ever wrote), `filter_out_wt_reseq` and
`get_wt_reseq_id` (helpers with no callers), `STARTING_STRAIN_ALE_ID = "0"` (which hid ALE 0
from the ALE *picker* while its samples went on landing in every analysis), and
`AleExperimentFilter.starting_strain_mutations` (a hand-curated list of mutation ids, since
migrated into delete changesets).

## Why this is not in `aledb_filter`

`aledb_filter` is the *reader's* filter, and its module docstrings spend a long time saying
so: per-person, session-scoped, ephemeral, stored in no table of ours, and clearable in one
click. This is the opposite on every count.

- **It belongs to the dataset, not the reader.** Two people looking at one experiment see the
  same subtraction.
- **It is not optional.** There is no toggle and no query parameter. `filter_observed_mutations`
  takes `view_filter=None` to mean unfiltered; there is no equivalent here.
- **It reaches further.** aledb-phylogeny never touches the filter layer, and this still
  applies to it.

Putting an unconditional shared exclusion inside `aledb_filter` would undo the distinction
that package exists to draw, so it lives here instead, beside the column it reads.

## What subtraction means

Every `Mutation` observed in the ancestor sample is removed from every other sample, and the
ancestor sample itself is removed from every listing. Matching is by `Mutation` primary key,
which is the identity the schema already uses -- one `Mutation` row per experiment per
(reference, position, type, change), observed many times.

**A mutation in the ancestor that is missing from a sample is fine.** It may simply have been
miscalled there. Nothing requires an ancestral mutation to be present everywhere before it is
subtracted: it matches what it matches, and matches nothing where it was not called.

## Not stored

There is no derived table here and no rebuilder to register. The exclusion is a subquery
evaluated by the database on the read that needs it, so it cannot go stale, needs no cache
and needs no `ensure_fresh` on any read path. `request_rebuild` is still called when the
designation *changes*, because everything downstream of it -- the dashboard's stored counts,
aledb-phylogeny's cached trees -- is stored and does go stale.
"""

import logging

from aledb_experiment.models import AleExperiment
from aledb_experiment import paths

logger = logging.getLogger(__name__)


def get_ancestor(experiment_id):
    """The sample designated as this experiment's ancestor, or None.

    Takes an id rather than an experiment so the query layers below can call it without
    holding a model, which is what lets `exclude_ancestry` be a one-line addition at a call
    site that only ever had an id.
    """
    if experiment_id in (None, "", "all"):
        return None
    return (AleExperiment.objects.filter(ale_id=experiment_id)
            .select_related("ancestor")
            .values_list("ancestor", flat=True)
            .first())


def ancestral_mutation_ids(experiment_id):
    """The `Mutation` ids observed in the designated ancestor, as a frozenset.

    **For highlighting only.** `exclude_ancestry` uses a subquery instead: materialising ids
    to hand back to the database is a round trip that buys nothing, and a sample carrying
    thousands of calls would push the parameter count somewhere neither backend enjoys. The
    per-sample breseq page genuinely wants the set, because it tests membership per rendered
    row in Python.
    """
    from aledb_seq.models import ObservedMutation

    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return frozenset()
    return frozenset(ObservedMutation.objects
                     .filter(sequencing_experiment_id=ancestor_id)
                     .values_list("mutation_id", flat=True))


def exclude_ancestry(observed_mutation_queryset, experiment_id):
    """Subtract the designated ancestor from a queryset of observations.

    Two exclusions, and both are needed. Dropping the ancestor from a sample listing removes
    its *column* from a table but leaves its mutations in every other sample; excluding the
    mutations alone would leave the ancestor standing as an empty column.

    Returns the queryset **untouched** when nothing is designated, which is the common case
    and adds no SQL to it -- not an empty `.exclude()`, which this repo has a scar from:
    an empty `Q` handed to `.exclude()` excludes everything, and once emptied a whole
    experiment.
    """
    from aledb_seq.models import ObservedMutation

    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return observed_mutation_queryset

    ancestral = (ObservedMutation.objects
                 .filter(sequencing_experiment_id=ancestor_id)
                 .values("mutation_id"))
    return (observed_mutation_queryset
            .exclude(sequencing_experiment_id=ancestor_id)
            .exclude(mutation_id__in=ancestral))


def exclude_all_ancestry(observed_mutation_queryset):
    """`exclude_ancestry` for a queryset that spans experiments.

    Search, the public interop API and the dashboard's totals all query across experiments,
    so there is no single ancestor to name -- and a query per experiment would be absurd on a
    page that is already scanning the installation.

    One exclusion covers all of them, and the schema is what makes that safe: `Mutation` rows
    are per experiment ("two experiments that call the same variant get their own rows"), so a
    mutation id observed in one experiment's ancestor cannot appear in another's samples. The
    global set is therefore unambiguous rather than merely convenient.
    """
    from aledb_seq.models import ObservedMutation

    ancestors = AleExperiment.objects.filter(ancestor__isnull=False).values("ancestor")
    if not ancestors.exists():
        return observed_mutation_queryset

    ancestral = (ObservedMutation.objects.filter(sequencing_experiment__in=ancestors)
                 .values("mutation_id"))
    return (observed_mutation_queryset
            .exclude(sequencing_experiment__in=ancestors)
            .exclude(mutation_id__in=ancestral))


def exclude_ancestor_samples(reseq_queryset, experiment_id=None):
    """Drop designated ancestors from a `ResequencingExperiment` queryset.

    With an `experiment_id`, drops that experiment's ancestor. Without one the queryset spans
    experiments -- the dashboard's site-wide counts -- so it drops every experiment's, which
    is one `exclude` against the set of designated samples rather than a query per experiment.
    """
    if experiment_id in (None, "", "all"):
        designated = (AleExperiment.objects.filter(ancestor__isnull=False)
                      .values("ancestor"))
        return reseq_queryset.exclude(pk__in=designated)

    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return reseq_queryset
    return reseq_queryset.exclude(pk=ancestor_id)


def describe_ancestor(experiment_id):
    """What a page should say about the subtraction it just did, or None.

    Returns `{'name': str, 'sample_id': int}`. None means nothing is designated and a page
    should say nothing -- as opposed to saying "no ancestor", which is a fact nobody reading
    a mutation table asked for.
    """
    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return None

    from aledb_seq.models import ResequencingExperiment

    reseq = (ResequencingExperiment.objects
             .select_related(paths.to_ale())
             .filter(pk=ancestor_id).first())
    if reseq is None:
        # SET_NULL should make this unreachable; a page saying nothing beats a page 500ing.
        logger.warning("experiment %s designates a sample that is gone", experiment_id)
        return None
    return {"name": reseq.ale_flask_isolate_str, "sample_id": reseq.pk}


def note_sample_deleted(sender, instance, **kwargs):
    """Mark everything stale when the sample being deleted is a designated ancestor.

    Connected to `pre_delete` rather than `post_delete`, because by the time the row is gone
    `SET_NULL` has already cleared the column and there is no way left to tell that this was
    the ancestor.

    Without this the designation vanishes silently and the derived data does not: aledb-
    phylogeny's cached trees would still have been inferred with those mutations subtracted,
    the dashboard totals would still exclude them, while convergence and fixation -- computed
    per request -- would flip on the next page load. Half the site would disagree with the
    other half, with nothing to explain why.

    Marking only. `request_rebuild` is one UPDATE and is safe from anywhere, including the
    middle of a cascade deleting a whole experiment; running the rebuilds here would mean
    recomputing derived data for rows that are in the process of being destroyed.
    """
    experiment_ids = list(AleExperiment.objects
                          .filter(ancestor=instance)
                          .values_list("ale_id", flat=True))
    if not experiment_ids:
        return

    from aledb_common.rebuild_registry import request_rebuild
    for experiment_id in experiment_ids:
        logger.info("experiment %s is losing its designated ancestor to a deletion",
                    experiment_id)
        request_rebuild(experiment_id, reason="ancestor sample deleted")

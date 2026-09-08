"""The designated ancestor, and the subtraction that follows from it.

An ALE starts from an ancestor, and that ancestor already differs from the reference genome.
Those differences are the starting line rather than evolution: convergence should not count
them as convergent, fixation should not report them fixed in every flask because they were
there before the first one, and the needle plot should not plot them.

**One designation, one exclusion.** `Experiment.ancestor` names one sample. Everything
below derives from that column; there is nothing else to configure and nothing to keep in
sync. It replaced four half-built spellings of the same idea, none of which subtracted
anything: `Population.starting_strain` (a FK no code path ever wrote), `filter_out_wt_sample` and
`get_wt_sample_id` (helpers with no callers), `STARTING_STRAIN_ALE_ID = "0"` (which hid ALE 0
from the ALE *picker* while its samples went on landing in every analysis), and
`AleExperimentFilter.starting_strain_mutations` (a hand-curated list of mutation ids, since
migrated into delete edit sets).

## Why this is not in `mutint_filter`

`mutint_filter` is the *reader's* filter, and its module docstrings spend a long time saying
so: per-person, session-scoped, ephemeral, stored in no table of ours, and clearable in one
click. This is the opposite on every count.

- **It belongs to the dataset, not the reader.** Two people looking at one experiment see the
  same subtraction.
- **The subtraction is not optional.** `filter_mutation_calls` takes `view_filter=None` to
  mean unfiltered; nothing that derives has an equivalent here. What a reader *can* choose is
  whether the two mutation tables **draw** the subtracted rows -- see `ancestral_shown` at
  the foot of this module -- and nothing computed reads that choice.
- **It reaches further.** mutint-phylogeny never touches the filter layer, and this still
  applies to it.

Putting an unconditional shared exclusion inside `mutint_filter` would undo the distinction
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
mutint-phylogeny's cached trees -- is stored and does go stale.
"""

import logging

from mutint_experiment.models import Experiment
from mutint_experiment import paths

logger = logging.getLogger(__name__)


def get_ancestor(experiment_id):
    """The sample designated as this experiment's ancestor, or None.

    Takes an id rather than an experiment so the query layers below can call it without
    holding a model, which is what lets `exclude_ancestry` be a one-line addition at a call
    site that only ever had an id.
    """
    if experiment_id in (None, "", "all"):
        return None
    return (Experiment.objects.filter(pk=experiment_id)
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
    from mutint_sample.models import MutationCall

    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return frozenset()
    return frozenset(MutationCall.objects
                     .filter(sample_id=ancestor_id)
                     .values_list("mutation_id", flat=True))


def exclude_ancestry(mutation_call_queryset, experiment_id):
    """Subtract the designated ancestor from a queryset of calls.

    Two exclusions, and both are needed. Dropping the ancestor from a sample listing removes
    its *column* from a table but leaves its mutations in every other sample; excluding the
    mutations alone would leave the ancestor standing as an empty column.

    Returns the queryset **untouched** when nothing is designated, which is the common case
    and adds no SQL to it -- not an empty `.exclude()`, which this repo has a scar from:
    an empty `Q` handed to `.exclude()` excludes everything, and once emptied a whole
    experiment.
    """
    from mutint_sample.models import MutationCall

    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return mutation_call_queryset

    ancestral = (MutationCall.objects
                 .filter(sample_id=ancestor_id)
                 .values("mutation_id"))
    return (mutation_call_queryset
            .exclude(sample_id=ancestor_id)
            .exclude(mutation_id__in=ancestral))


def exclude_all_ancestry(mutation_call_queryset):
    """`exclude_ancestry` for a queryset that spans experiments.

    Search, mutint-api's cross-experiment lookup and the dashboard's totals all query across experiments,
    so there is no single ancestor to name -- and a query per experiment would be absurd on a
    page that is already scanning the installation.

    One exclusion covers all of them, and the schema is what makes that safe: `Mutation` rows
    are per experiment ("two experiments that call the same variant get their own rows"), so a
    mutation id observed in one experiment's ancestor cannot appear in another's samples. The
    global set is therefore unambiguous rather than merely convenient.
    """
    from mutint_sample.models import MutationCall

    ancestors = Experiment.objects.filter(ancestor__isnull=False).values("ancestor")
    if not ancestors.exists():
        return mutation_call_queryset

    ancestral = (MutationCall.objects.filter(sample__in=ancestors)
                 .values("mutation_id"))
    return (mutation_call_queryset
            .exclude(sample__in=ancestors)
            .exclude(mutation_id__in=ancestral))


def exclude_ancestor_samples(sample_queryset, experiment_id=None):
    """Drop designated ancestors from a `Sample` queryset.

    With an `experiment_id`, drops that experiment's ancestor. Without one the queryset spans
    experiments -- the dashboard's site-wide counts -- so it drops every experiment's, which
    is one `exclude` against the set of designated samples rather than a query per experiment.
    """
    if experiment_id in (None, "", "all"):
        designated = (Experiment.objects.filter(ancestor__isnull=False)
                      .values("ancestor"))
        return sample_queryset.exclude(pk__in=designated)

    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return sample_queryset
    return sample_queryset.exclude(pk=ancestor_id)


def describe_ancestor(experiment_id):
    """What a page should say about the subtraction it just did, or None.

    Returns `{'name': str, 'sample_id': int}`. None means nothing is designated and a page
    should say nothing -- as opposed to saying "no ancestor", which is a fact nobody reading
    a mutation table asked for.
    """
    ancestor_id = get_ancestor(experiment_id)
    if ancestor_id is None:
        return None

    from mutint_sample.models import Sample

    sample = (Sample.objects
             .select_related(paths.to_population())
             .filter(pk=ancestor_id).first())
    if sample is None:
        # SET_NULL should make this unreachable; a page saying nothing beats a page 500ing.
        logger.warning("experiment %s designates a sample that is gone", experiment_id)
        return None
    return {"name": sample.label, "sample_id": sample.pk}


def note_sample_deleted(sender, instance, **kwargs):
    """Mark everything stale when the sample being deleted is a designated ancestor.

    Connected to `pre_delete` rather than `post_delete`, because by the time the row is gone
    `SET_NULL` has already cleared the column and there is no way left to tell that this was
    the ancestor.

    Without this the designation vanishes silently and the derived data does not: mutint-
    phylogeny's cached trees would still have been inferred with those mutations subtracted,
    the dashboard totals would still exclude them, while convergence and fixation -- computed
    per request -- would flip on the next page load. Half the site would disagree with the
    other half, with nothing to explain why.

    Marking only. `request_rebuild` is one UPDATE and is safe from anywhere, including the
    middle of a cascade deleting a whole experiment; running the rebuilds here would mean
    recomputing derived data for rows that are in the process of being destroyed.
    """
    experiment_ids = list(Experiment.objects
                          .filter(ancestor=instance)
                          .values_list("pk", flat=True))
    if not experiment_ids:
        return

    from mutint_common.rebuild_registry import request_rebuild
    for experiment_id in experiment_ids:
        logger.info("experiment %s is losing its designated ancestor to a deletion",
                    experiment_id)
        request_rebuild(experiment_id, reason="ancestor sample deleted")


# ---- The reader's display toggle ---------------------------------------------------------
#
# Whether the two mutation tables -- the per-sample page and Compare -- draw the rows the
# subtraction removed, tinted red. Display only: convergence, fixation, the phylogeny, the
# Overview, the dashboard and the exports subtract exactly as above whatever this says. It is
# remembered the way `mutint_filter.view_filter.get_view_filter` remembers the reader's filter,
# and for the same reason: the sidebar's links carry no parameters, so the choice has to follow
# the reader from page to page. Hidden by default, because the rows are the starting line
# rather than evolution, and a page that opens showing them reads as one that did not subtract.

#: The link's query parameter and its two values. Present in the URL it wins and is remembered;
#: absent, the session decides; absent there too, hidden.
ANCESTRAL_PARAM = "ancestral"
ANCESTRAL_SHOW = "show"
ANCESTRAL_HIDE = "hide"
#: `{"<experiment_id>": True}` for every experiment this reader has chosen to show. Hiding
#: forgets the entry, the way an empty filter is forgotten. String keys, because the session's
#: JSON serializer hands integer keys back as strings.
ANCESTRAL_SESSION_KEY = "mutint_ancestral_shown"
MAX_REMEMBERED_EXPERIMENTS = 20


def ancestral_shown(request, experiment_id):
    """Whether this reader wants the subtracted rows drawn on this experiment's tables.

    Resolution, in `get_view_filter`'s order: the parameter when it is in the query string,
    remembered into the session; otherwise the session; otherwise False. Memoised on the
    request, so the view that builds the rows and the summary tag that describes them cannot
    disagree.

    The GET-write is the one `get_view_filter` already makes, with the same argument -- what is
    written is idempotent, carries no authority, and is a display preference the page states
    out loud. The one difference is direction: this *widens* what is drawn rather than
    narrowing it, but only to rows the reader can already open on the ancestor's own page.
    """
    cache = getattr(request, "_mutint_ancestral_shown_cache", None)
    if cache is None:
        cache = request._mutint_ancestral_shown_cache = {}
    key = str(experiment_id)
    if key in cache:
        return cache[key]

    stored = request.session.get(ANCESTRAL_SESSION_KEY)
    stored = dict(stored) if isinstance(stored, dict) else {}
    if ANCESTRAL_PARAM in request.GET:
        shown = request.GET.get(ANCESTRAL_PARAM) == ANCESTRAL_SHOW
        # Rebuild and reassign the top-level dict: assigning into the nested one leaves
        # `session.modified` False and loses the write with no error.
        stored.pop(key, None)
        if shown:
            stored[key] = True
        while len(stored) > MAX_REMEMBERED_EXPERIMENTS:
            del stored[next(iter(stored))]
        request.session[ANCESTRAL_SESSION_KEY] = stored
    else:
        shown = bool(stored.get(key))

    cache[key] = shown
    return shown


def ancestral_toggle_url(request, shown):
    """The link that flips the state, keeping every other parameter of the page it is on."""
    params = request.GET.copy()
    params[ANCESTRAL_PARAM] = ANCESTRAL_HIDE if shown else ANCESTRAL_SHOW
    return "?" + params.urlencode()

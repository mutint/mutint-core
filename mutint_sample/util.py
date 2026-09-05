import collections
import mutint_sample.models
from mutint_common.util import is_int
from mutint_experiment.ordering import sample_order, sample_sort_key
from mutint_filter.util import filter_mutation_calls
from mutint_common.constants import SAMPLE_TYPE_MIXED
from mutint_experiment import paths

HTML_ECOCYC = """<a href = "https://ecocyc.org/ECOLI/substring-search?type=GENE&object={gene}">{gene}</a>"""


def get_mutation_call_queryset(experiment_id):
    """Every call this experiment holds -- what is *stored*, ancestor included.

    **Usually not what you want.** An experiment may designate an ancestor, whose mutations
    are the starting line rather than evolution; anything analyzing or summarizing the data
    wants `get_evolved_call_queryset` below. This raw form is for the three places
    that mean "what is stored": the CSV export, the mutation editor, and the per-sample
    breseq page, which tints ancestral rows rather than hiding them.
    """
    return mutint_sample.models.MutationCall.objects.filter(**{paths.to_experiment_id(paths.FROM_CALL): experiment_id})


def get_evolved_call_queryset(experiment_id):
    """This experiment's calls with its designated ancestor subtracted.

    The default choice for anything that analyzes or counts. See
    `mutint_experiment/ancestor.py` for what subtraction means and why it is not the reader's
    filter. With no ancestor designated this is `get_mutation_call_queryset` exactly.
    """
    from mutint_experiment.ancestor import exclude_ancestry
    return exclude_ancestry(get_mutation_call_queryset(experiment_id), experiment_id)


def get_all_calls_filtered(experiment_id, *, filter_type=None, view_filter=None):
    """An experiment's calls, through the reader's filter.

    `view_filter` comes from `mutint_filter.view_filter.get_view_filter(request, experiment_id)`
    and is the reader's own; None means unfiltered. It replaced `skip_experiment_filter`, which
    asked to see through a *shared* filter -- a question that stops meaning anything once the
    filter is yours to clear.
    """
    queryset = get_evolved_call_queryset(experiment_id)
    return filter_mutation_calls(queryset, filter_type=filter_type, view_filter=view_filter)


def calls_for_samples(sample_id_list, experiment_id):
    """Calls in these samples, with the experiment's designated ancestor subtracted.

    The entry point for a plugin that derives something. It was `get_all_calls`,
    which did the `filter` half and had no callers left -- mutint-compare, mutint-converge,
    mutint-fixation and mutint-phylogeny had each written that one line out by hand instead.

    That hand-copying is exactly what this fixes. Dropping the ancestor from a *sample* list
    removes its column from a table but leaves its mutations sitting in every other sample --
    and because an ancestral mutation is present in every ALE, convergence would report all of
    them as convergent and fixation all of them as fixed. **The subtraction has to reach the
    derivation, not the render**, the same rule `docs/plugin/filtering.md` states for the
    reader's filter and for the same reason.
    """
    from mutint_experiment.ancestor import exclude_ancestry
    queryset = mutint_sample.models.MutationCall.objects.filter(
        sample_id__in=sample_id_list)
    return exclude_ancestry(queryset, experiment_id)


def get_ordered_reseq_queryset(experiment_id, ale_id=None, sample_type=None, *,
                               include_ancestor=False):
    """An experiment's samples in A/F/I/R order, without its designated ancestor.

    **The ancestor is excluded by default**, and the few callers that curate rather than
    read pass `include_ancestor=True`: the Edit-samples page and the mutation editor, which
    must still be able to see and change it.

    Defaulting this way round is deliberate, and it is the opposite of what
    `get_mutation_call_queryset` does. The two mistakes are not symmetric. Forgetting to
    opt *in* hides the ancestor from a curation page, which is visible and gets reported the
    same day; forgetting to opt *out* leaves ancestral data in an analysis, which is
    invisible and wrong. It also means mutint-compare, mutint-converge and mutint-fixation need
    no edit here at all.

    Keyword-only: this module already carries a scar from `get_reseq_ordered_dict` being
    called with a `request` in the `sample_type` slot, which silently dropped every
    population sample from two plugin pages.
    """
    reseq_qryset = mutint_sample.models.Sample.objects.select_related(
        paths.to_experiment()
    ).order_by(*sample_order())
    if experiment_id:
        reseq_qryset = reseq_qryset.filter(**{paths.to_experiment_id(): experiment_id})
    # `is not None`, not truthiness: the starting strain's ALE is "0"
    # (`common.STARTING_STRAIN_ALE_ID`), which was falsy while this column held integers and
    # so quietly selected every ALE instead of that one.
    if ale_id is not None and ale_id != "":
        reseq_qryset = reseq_qryset.filter(**{paths.to_population_label(): ale_id})
    if sample_type:
        # Two named filters rather than a computed boolean. `get_sample_type` has already
        # refused anything that is not one of the two, so this is a genuine two-way choice
        # -- it used to be "population, or else clonal", which silently subset the page.
        reseq_qryset = reseq_qryset.filter(
            **(paths.mixed_filter() if sample_type == SAMPLE_TYPE_MIXED
               else paths.clonal_filter()))
    if not include_ancestor:
        from mutint_experiment.ancestor import exclude_ancestor_samples
        reseq_qryset = exclude_ancestor_samples(reseq_qryset, experiment_id)
    return reseq_qryset


def get_reseq_ordered_dict(experiment_id, population=None, sample_type=None,
                           *, include_ancestor=False):
    """An experiment's samples as `{id: Sample}`, in the order their columns should appear.

    `population` and `sample_type` narrow the set; `include_ancestor` keeps the designated
    ancestor, for a page that curates rather than reads. See `get_ordered_reseq_queryset`,
    which this wraps.

    It took a `request` too, for a `tag_select` query parameter that showed or hid sample
    columns by their tags. Tagging is gone; the parameter went first, so a caller cannot pass
    one and believe it filtered.
    """
    reseq_queryset = get_ordered_reseq_queryset(experiment_id, population, sample_type,
                                                include_ancestor=include_ancestor)
    return collections.OrderedDict((reseq.id, reseq) for reseq in reseq_queryset)


def get_mutations_from_calls(mutation_calls):
    mut_map = {call.mutation.id: call.mutation for call in mutation_calls}
    return mut_map.values()


def get_ordered_reseq_dict(mutation_calls):
    """The samples appearing in these calls, `{id: reseq}`, in A/F/I/R order.

    **It sorts rather than trusting what it was handed.** The name said "ordered" and nothing
    here did any ordering: the dict came out in first-appearance order, which is A/F/I/R only
    because `filter_mutation_calls` applies `sample_order` two modules away. Its one
    caller is the CSV export, so every exported file's column order rested on an `order_by`
    that carries no comment saying anything depends on it -- and which
    `filtered_mutation_call_queryset` explicitly warns callers to strip before
    aggregating. Sorting here costs nothing on a list already in the right order and makes
    the guarantee local to the function that claims it.

    Still only the samples that *appear*: a sample with no calls left after filtering
    gets no column, where the on-screen table builds its columns from the sample list and so
    keeps an empty one. That difference is left alone -- a CSV of the rows it contains is a
    defensible thing for an export to be -- but it is a difference, not an oversight.
    """
    by_id = {call.sample.id: call.sample
             for call in mutation_calls}
    return collections.OrderedDict(
        (reseq.id, reseq) for reseq in sorted(by_id.values(), key=sample_sort_key))


def get_ecocyc_gene_list(gene_list, is_ecocyc_gene: bool = False):
    url_list = []
    for each in gene_list:
        if each.startswith("<"):
            each = each.split(">")[-1]
        if is_ecocyc_gene:
            url_list.append(HTML_ECOCYC.format(gene=each))
        else:
            url_list.append(each)
    return url_list


def get_ref_sequences():
    return sorted(
        mutint_sample.models.Mutation.objects.exclude(seq_id__isnull=True).exclude(seq_id='')
        .values_list('seq_id', flat=True).distinct()
    )


def get_matching_call_ids(mutation_id, experiment_id):
    local_mutation_calls = mutint_sample.models.MutationCall.objects.filter(
        **{paths.to_experiment_id(paths.FROM_CALL): experiment_id},
        mutation__id=mutation_id).order_by(*sample_order("sample__"))
    matching_call_ids = []
    for local_mutation_call in local_mutation_calls:
        matching_call_ids.append(local_mutation_call.id)
    return matching_call_ids

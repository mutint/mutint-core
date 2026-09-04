import collections
import json
import logging
import re
from urllib.parse import quote

from aledb_experiment.models import Experiment, Project
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from aledb_experiment.ancestor import exclude_all_ancestry
from aledb_filter.util import filter_mutation_calls, filtered_mutation_call_queryset
from aledb_filter.view_filter import PARAMS as FILTER_PARAMS, ViewFilter
from aledb_common.logger import user_extra
from aledb_common.constants import SAMPLE_TYPE_CLONAL, SAMPLE_TYPE_MIXED
from aledb_sample.util import get_ordered_reseq_dict, get_ordered_reseq_queryset
from aledb_sample.models import MutationCall
from aledb_experiment import paths

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r'<[^>]+>')
_GENE_SEP_RE = re.compile(r'[,|;]')
_VALID_GENE_RE = re.compile(r'[A-Za-z0-9]')
_BASE_SEARCH_URL = "https://aledb.org/search/"


def _strip_html(text):
    """Extract gene name from potentially HTML-wrapped text.

    e.g. '<i><b>168 genes</b><BR>yjgN' -> 'yjgN'
    """
    if '<BR>' in text:
        text = text.rsplit('<BR>', 1)[-1]
    return _HTML_TAG_RE.sub('', text).strip()


def _requested_filter(request):
    """The filter this caller asked for, from `min_freq` / `max_freq` / `ignore_genes`.

    **Unfiltered by default**, which is a change. This endpoint used to apply whatever
    `AleExperimentFilter` row each experiment happened to carry -- so an anonymous caller got a
    view shaped by a setting they could not see, could not choose, and would not have known was
    there. That table is gone; filtering belongs to whoever is looking, and out here that is the
    caller, so they say what they want in the query string.

    The same three parameter names the pages use, parsed by the same `ViewFilter`, so the API and
    the UI cannot disagree about what `min_freq=20` means.

    Raises `ValueError` on anything unusable, which the endpoints turn into a 400. The pages fall
    back to unfiltered instead, because a reader can see the controls and correct them; a caller
    who sent `min_freq=abc` and got everything back would have a wrong answer dressed as a right
    one.
    """
    if not any(name in request.GET for name in FILTER_PARAMS):
        return None
    return ViewFilter.from_params(request.GET)


def _public_queryset(view_filter=None):
    """Calls in public projects, through the caller's filter.

    The cutoff half goes through the shared `filtered_mutation_call_queryset`, replacing a
    hand-rewritten copy of that `Q` that lived here and had already drifted: it skipped gene
    filtering entirely, with a comment saying doing it per row was too slow for the whole
    dataset. That is still true of the row-level subset test, and the two gene endpoints below
    do not need it -- see `_without_ignored_genes`.
    """
    queryset = MutationCall.objects.filter(
        **{paths.to_experiment(paths.FROM_CALL, 'project__is_public'): True}
    )
    # Designated ancestors are subtracted here too, and unlike the cutoff above that is not
    # something the caller chose. An anonymous caller getting rows that every page on the site
    # excludes would be a wrong answer dressed as a right one -- the same reason `parse` raises
    # on a malformed `min_freq` rather than quietly returning everything.
    queryset = exclude_all_ancestry(queryset)
    queryset, _ = filtered_mutation_call_queryset(queryset, view_filter=view_filter)
    return queryset


def _without_ignored_genes(names, view_filter):
    """Drop the genes a caller asked to ignore, from a list of individual gene names.

    **Deliberately a different rule from `gene_is_filtered`**, and the difference is the unit.
    That one asks whether a *mutation* is hidden, and answers yes only when every gene it touches
    is ignored -- so an intergenic call between an ignored gene and a kept one still counts as
    evidence about the kept one. Here the answer *is* a list of gene names, so "do not list a
    gene I asked you to ignore" is the whole of it, and it costs a set lookup rather than a walk
    over every row.
    """
    if view_filter is None or not view_filter.genes:
        return names
    ignored = view_filter.genes_set
    return [name for name in names if name not in ignored]


@csrf_exempt
@require_http_methods(["GET"])
def genes(request):
    """
    Returns a list of all unique genes from mutations in public projects.
    """
    try:
        view_filter = _requested_filter(request)
        mut_qryset = _public_queryset(view_filter)

        # Extract unique genes
        genes_list = mut_qryset.values_list(
            'mutation__gene', flat=True
        ).distinct().order_by('mutation__gene')
        
        # Split comma-separated genes, strip HTML tags, and flatten
        individual_genes = set()
        for gene_entry in genes_list:
            if gene_entry:
                clean = _strip_html(gene_entry)
                for g in _GENE_SEP_RE.split(clean):
                    g = g.strip()
                    if g and _VALID_GENE_RE.search(g):
                        individual_genes.add(g)
        
        # Convert to sorted list of dicts with URL

        genes_list = _without_ignored_genes(sorted(individual_genes), view_filter)
        genes_with_urls = [
            {
                "gene": gene,
                "url": f"{_BASE_SEARCH_URL}?hidden_columns=&gene={quote(gene)}&min_freq=&max_freq=&ref_seq=&min_pos=&max_pos=&mut_type=&project=&strain="
            }
            for gene in genes_list
        ]

        return JsonResponse({"genes": genes_with_urls})

    except ValueError as bad_request:
        return JsonResponse({'error': str(bad_request)}, status=400)
    except Exception as e:
        logger.exception("genes endpoint error", extra=user_extra(request))
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def strains(request):
    """return list of strains"""
    logger.info("list strains", extra=user_extra(request))
    try:
        view_filter = _requested_filter(request)
        mut_qryset = _public_queryset(view_filter)
        strain_values = mut_qryset.values_list(
            paths.to_population(paths.FROM_CALL, 'strain'), flat=True
        ).distinct()
        strains = sorted([s for s in strain_values if s and s != " N/A"])

        strains_with_urls = [
            {
                "strain": strain,
                "url": f"{_BASE_SEARCH_URL}?hidden_columns=&gene=&min_freq=&max_freq=&ref_seq=&min_pos=&max_pos=&mut_type=&project=&strain={quote(strain)}"
            }
            for strain in strains
        ]
        return JsonResponse({"strains": strains_with_urls})
    except ValueError as bad_request:
        return JsonResponse({'error': str(bad_request)}, status=400)
    except Exception as e:
        logger.exception("strains endpoint error", extra=user_extra(request))
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def gene_strain_pairs(request):
    """Returns all unique gene/strain pairs with search URLs."""
    logger.info("list gene-strain pairs", extra=user_extra(request))
    try:
        view_filter = _requested_filter(request)
        pairs_qs = _public_queryset(view_filter).values_list(
            'mutation__gene',
            paths.to_population(paths.FROM_CALL, 'strain'),
        ).distinct()

        # Expand comma-separated genes, strip HTML tags, into individual pairs
        unique_pairs = set()
        for gene_entry, strain in pairs_qs:
            if not gene_entry or not strain:
                continue
            clean = _strip_html(gene_entry)
            for gene in _GENE_SEP_RE.split(clean):
                gene = gene.strip()
                if gene and _VALID_GENE_RE.search(gene):
                    unique_pairs.add((gene, strain))


        _kept_genes = set(_without_ignored_genes(
            sorted({gene for gene, _ in unique_pairs}), view_filter))
        pairs_with_urls = [
            {
                "gene": gene,
                "strain": strain,
                "url": f"{_BASE_SEARCH_URL}?hidden_columns=&gene={quote(gene)}&min_freq=&max_freq=&ref_seq=&min_pos=&max_pos=&mut_type=&project=&strain={quote(strain)}"
            }
            for gene, strain in sorted(unique_pairs)
            if gene in _kept_genes
        ]

        return JsonResponse({"pairs": pairs_with_urls, "count": len(pairs_with_urls)})

    except ValueError as bad_request:
        return JsonResponse({'error': str(bad_request)}, status=400)
    except Exception as e:
        logger.exception("gene-strain-pairs endpoint error", extra=user_extra(request))
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def query_by_pair(request):
    logger.info("query by pair", extra=user_extra(request))
    try:
        data = json.loads(request.body)
        pairs = data.get("pairs", [])
        if isinstance(pairs, dict):  # allow a single object
            pairs = [pairs]

        if not pairs:
            return JsonResponse({
                "mutations": [],
                "count": 0,
                "message": "No gene/strain pairs provided"
            })

        return _run_query(
            request,
            pairs,
            q_builder=lambda p: (
                Q(**{paths.to_population(paths.FROM_CALL, 'strain'): p.get("strain", "").strip()}) &
                Q(mutation__gene__icontains=p.get("gene", "").strip())
            ) if p.get("gene") and p.get("strain") else None,
            empty_msg="No gene/strain pairs provided",
            invalid_msg="No valid gene/strain pairs provided",
            search_gene=pairs[0].get("gene", "").strip() if pairs else None
        )

    except json.JSONDecodeError:
        return JsonResponse({
            "mutations": [],
            "count": 0,
            "message": "Invalid JSON"
        }, status=400)
    except Exception as e:
        logger.exception("search error", extra=user_extra(request))
        return JsonResponse({
            "mutations": [],
            "count": 0,
            "message": f"Error: {e}"
        }, status=500)


@csrf_exempt
@require_POST
def query_by_strain(request):
    logger.info("query by strain", extra=user_extra(request))
    try:
        ids = _parse_ids(request, 'ids')
        return _run_query(
            request,
            ids,
            q_builder=lambda strain: Q(**{paths.to_population(paths.FROM_CALL, 'strain'): strain}),
            empty_msg='No strains provided',
            invalid_msg='No valid strains provided'
        )
    except json.JSONDecodeError:
        return JsonResponse({'mutations': [], 'count': 0, 'message': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.exception("search broke", extra=user_extra(request))
        return JsonResponse({'mutations': [], 'count': 0, 'message': f'Error: {e}'}, status=500)


@csrf_exempt
@require_POST
def query_by_gene(request):
    logger.info("query by gene", extra=user_extra(request))
    try:
        ids = _parse_ids(request, 'ids')
        return _run_query(
            request,
            ids,
            q_builder=lambda gene: Q(mutation__gene__icontains=gene),
            empty_msg='No genes provided',
            invalid_msg='No valid genes provided',
            search_gene=ids[0] if ids else None
        )
    except json.JSONDecodeError:
        return JsonResponse({'mutations': [], 'count': 0, 'message': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.exception("search broke", extra=user_extra(request))
        return JsonResponse({'mutations': [], 'count': 0, 'message': f'Error: {e}'}, status=500)


def _parse_ids(request, key):
    """Return list of IDs from request JSON body under `key`."""
    data = json.loads(request.body)
    ids = data.get('ids', [])
    return [ids] if isinstance(ids, str) else ids


def _serialize_metadata(metadata_list):
    out = []
    for m in metadata_list:
        item = {
            'experiment_id':   m['experiment_id'],
            'experiment_name': m['experiment_name'],
            'project_id':      m['project_id'],
            'project_name':    m['project_name'],
            'multiple':            m['multiple'],
        }

        item['sample_info_list'] = m.get('sample_info_list', [])
        out.append(item)
    return out


#: One dict per sample, published as `sample_info_list`. It was
#: `aledb_metadata.views.get_sample_info_list`, shared with the `/metadata` page; that page
#: and its app are gone and this endpoint is the only consumer left, so the builder lives
#: beside the payload it exists for.
#:
#: **Eight keys went with the `Media` table** -- `media_description`, `carbon_source`,
#: `nitrogen_source`, `phosphorus_source`, `sulfur_source`, `calcium_source`, `supplement`
#: and `temperature`. Nothing stores those now, so a consumer asking for one gets a KeyError
#: rather than an empty string that reads like "this experiment recorded no carbon source".
#: That is the same posture every earlier correction to this payload took: no key is ever
#: emitted under two names, or kept as a hollow shell.
#:
#: `sample_medium_description` stays, and keeps its long name. It is the sample's own note
#: rather than the medium's, it is still written -- the sample edit page edits it -- and it
#: was never the ambiguous half of that pair.
def _sample_info_list(reseq_queryset):
    rows = []
    for reseq in reseq_queryset:
        population = reseq.population
        rows.append({
            'label': reseq.label,
            'sample_type': (SAMPLE_TYPE_MIXED if reseq.is_mixed else SAMPLE_TYPE_CLONAL),
            'sample_medium_description': reseq.curation.get("medium_description", ""),
            'strain': population.strain,
            'population_description': population.description,
            # **These four keys do not move**, though the fields behind them did and two
            # of them shortened inside their group. This payload has an external consumer,
            # so the translation lives here rather than in the key names.
            'library_prep': reseq.sequencing.get("library_prep", ""),
            'reference_genome': reseq.sequencing.get("reference_genome", ""),
            'breseq_version': reseq.breseq.get("version", ""),
            'sequencing_date': reseq.sequencing.get("date", ""),
            'experiment_name': population.experiment.name,
        })
    return rows

def _extract_url_gene(raw_gene, search_gene=None):
    """Pick the best gene name for the search URL.

    From a raw gene field (e.g. 'ADH1, YOL085C'), extract individual gene names
    and return the one matching the user's search term. Falls back to the first
    cleaned gene name, or the raw value.
    """
    if not raw_gene:
        return ''
    clean = _strip_html(raw_gene)
    parts = [g.strip() for g in _GENE_SEP_RE.split(clean) if g.strip()]
    if not parts:
        return clean
    if search_gene:
        search_lower = search_gene.lower()
        for p in parts:
            if search_lower in p.lower():
                return p
    return parts[0]


def _serialize_mutations(mutations, search_gene=None):
    out = []
    for m in mutations:
        gene = m.mutation.gene
        strain = m.sample.population.strain
        url_gene = _extract_url_gene(gene, search_gene)
        item = {
            'mutation_call_id': m.id,
            'mutation_id': m.mutation_id,
            'gene': gene,
            'position': m.mutation.start_position,
            'mutation_type': m.mutation.mutation_type,
            'sequence_change': m.mutation.sequence_change,
            'details': m.mutation.protein_change,
            'frequency': m.frequency,
            'ref_seq': m.mutation.seq_id,
            'strain': strain,
            'project_id': m.sample.population.experiment.project_id,
            'url': f"{_BASE_SEARCH_URL}?hidden_columns=&gene={quote(url_gene)}&min_freq=&max_freq=&ref_seq=&min_pos=&max_pos=&mut_type=&project=&strain={quote(strain or '')}",
        }

        exp = getattr(m, 'experiment', None)
        if isinstance(exp, dict):
            item['experiment'] = {
                'experiment_id': exp.get('experiment_id', m.sample.experiment.id),
                'sample_id': exp.get('sample_id', m.sample.id),
                # The sample's label with its experiment in front, which is what a caller
                # showing rows from several experiments needs. It was `sample_name` -- also
                # the name of a column, which held something else entirely.
                'sample_label': exp.get('label'),
                # **`genotype` held a formatted frequency.** Not a genotype, and not a
                # sample type despite the local name that used to build it: the string is
                # `"%2f"` of `MutationCall.frequency`, empty when the mutation is not
                # present in that sample. The same class of error as `knockouts` and
                # `taxonomy_id` above, found the same way -- by reading what the value is.
                'frequency': exp.get('frequency'),
            }
        else:
            pass

        out.append(item)
    return out


def _run_query(request, ids, q_builder, empty_msg, invalid_msg, search_gene=None):
    """
    Generic executor for the endpoints.
    `q_builder(item:str) -> Q` builds a Q object for a single id.

    The caller's filter is read from the query string even though these are POSTs -- one
    spelling across all six endpoints beats two, and a query string on a POST is ordinary.
    """
    if not ids:
        return JsonResponse({'mutations': [], 'count': 0, 'message': empty_msg})

    try:
        view_filter = _requested_filter(request)
    except ValueError as bad_request:
        return JsonResponse({'error': str(bad_request)}, status=400)

    public_project_q = Q(
        **{paths.to_experiment(paths.FROM_CALL, 'project__is_public'): True}
    )

    mutation_calls = []
    for item in ids:
        if not item:
            continue
        q = q_builder(item)
        if q is None:
            continue

        qs = exclude_all_ancestry(MutationCall.objects.filter(public_project_q & q))
        mutations = filter_mutation_calls(qs, view_filter=view_filter)
        logger.info("Found %d mutations for %s", len(mutations), item, extra=user_extra(request))
        mutation_calls.extend(mutations)

    if not mutation_calls:
        return JsonResponse({'mutations': [], 'count': 0, 'message': invalid_msg})

    # Sorted, not first-appearance. `mutation_calls` is a concatenation of one filtered
    # list per requested id; each block is in A/F/I/R order but the concatenation is not, and
    # a sample appearing in two blocks keeps the position of the first. `get_ordered_reseq_dict`
    # sorts, so the samples come out in the same order every other list on the site uses.
    reseq_dict = get_ordered_reseq_dict(mutation_calls)

    # A list, not a set: this is iterated below to build the response's metadata, and a set's
    # iteration order is not stable between runs -- so two identical requests could return
    # the experiments in different orders, which is exactly the kind of thing a caller
    # diffing two responses would chase for an afternoon.
    experiment_ids = []

    for mutation_call in mutation_calls:
        experiment_id = mutation_call.sample.experiment.id
        logging.info("Processing mutation with ID: %s", experiment_id, extra=user_extra(request))
        if experiment_id not in experiment_ids:
            experiment_ids.append(experiment_id)
        if mutation_call.sample_id in reseq_dict.keys():
            sample_name = reseq_dict[mutation_call.sample_id].qualified_label
            # Initialised here, and not only inside the branch below: it used to be assigned
            # nowhere else, so a row that failed the test either raised NameError or silently
            # reported the *previous* row's frequency.
            frequency = ""
            if mutation_call.present:
                frequency = ("%2f" % float(mutation_call.frequency)
                             if mutation_call.frequency is not None else "")
            mutation_call.experiment = {
                'experiment_id': mutation_call.sample.experiment.id,
                'sample_id': mutation_call.sample.id,
                'label': sample_name,
                'frequency': frequency,
            }

    metadata = []

    for experiment_id in sorted(experiment_ids):
        logging.info("Processing reseq experiment with ID: %s", experiment_id, extra=user_extra(request))
        experiment = Experiment.objects.get(pk=experiment_id)
        if experiment:
            reseq_queryset = get_ordered_reseq_queryset(experiment_id, None)
            sample_info_list = _sample_info_list(reseq_queryset)
            experiment_info = {
                "sample_info_list": sample_info_list,
                "experiment_name": experiment.name,
                "project_name": experiment.project.name,
                "project_id": experiment.project.id,
                "multiple": False,
                "experiment_id": experiment_id
            }
            metadata.append(experiment_info)
    
    mutations_data = _serialize_mutations(mutation_calls, search_gene=search_gene)
    experiment_metadata = _serialize_metadata(metadata)

    return JsonResponse({'mutations': mutations_data, 'experiment_metadata': experiment_metadata, 'count': len(mutations_data), 'message': 'Success'})




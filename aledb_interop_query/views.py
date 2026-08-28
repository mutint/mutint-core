import collections
import json
import logging
import re
from urllib.parse import quote

from aledb_experiment.models import AleExperiment, Project
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from aledb_filter.util import filter_observed_mutations, filtered_observed_mutation_queryset
from aledb_filter.view_filter import PARAMS as FILTER_PARAMS, ViewFilter
from aledb_common.logger import user_extra
from aledb_metadata.views import get_ordered_reseq_queryset, get_reseq_info_list
from aledb_seq.models import ObservedMutation

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
    """Observations in public projects, through the caller's filter.

    The cutoff half goes through the shared `filtered_observed_mutation_queryset`, replacing a
    hand-rewritten copy of that `Q` that lived here and had already drifted: it skipped gene
    filtering entirely, with a comment saying doing it per row was too slow for the whole
    dataset. That is still true of the row-level subset test, and the two gene endpoints below
    do not need it -- see `_without_ignored_genes`.
    """
    queryset = ObservedMutation.objects.filter(
        sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__project__is_public=True
    )
    queryset, _ = filtered_observed_mutation_queryset(queryset, view_filter=view_filter)
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
            'sequencing_experiment__tech_rep__isolate__flask__ale_id__strain', flat=True
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
            'sequencing_experiment__tech_rep__isolate__flask__ale_id__strain',
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
                Q(sequencing_experiment__tech_rep__isolate__flask__ale_id__strain=p.get("strain", "").strip()) &
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
            q_builder=lambda strain: Q(sequencing_experiment__tech_rep__isolate__flask__ale_id__strain=strain),
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
            'ale_experiment_id':   m['ale_experiment_id'],
            'ale_experiment_name': m['ale_experiment_name'],
            'ale_project_id':      m['ale_project_id'],
            'ale_project_name':    m['ale_project_name'],
            'multiple':            m['multiple'],
        }

        flat = []
        for tup in m.get('reseq_info_list', []):
            exp = tup[0]
            flat.append({
                'ale_flask_isolate_str':   exp.ale_flask_isolate_str,
                'clonal_or_population':    tup[1],
                'tech_rep_description':    tup[2],
                'media_description':       tup[3],
                'carbon_source':           tup[4],
                'nitrogen_source':         tup[5],
                'phosphorous_source':      tup[6],
                'sulfur_source':           tup[7],
                'calcium_source':          tup[8],
                'supplement':              tup[9],
                'temperature':             tup[10],
                'strain':                  tup[11],
                'knockouts':               tup[12],
                'taxonomy_id':             tup[13],
                'reseq_reference':         tup[14],
            })
        item['reseq_info_list'] = flat
        out.append(item)
    return out

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
        strain = m.sequencing_experiment.tech_rep.isolate.flask.ale_id.strain
        url_gene = _extract_url_gene(gene, search_gene)
        item = {
            'observed_mutation_id': m.id,
            'mutation_id': m.mutation_id,
            'gene': gene,
            'position': m.mutation.position,
            'mutation_type': m.mutation.mutation_type,
            'sequence_change': m.mutation.sequence_change,
            'details': m.mutation.protein_change,
            'frequency': m.frequency,
            'ref_seq': m.mutation.reseq_reference,
            'strain': strain,
            'project_id': m.sequencing_experiment.tech_rep.isolate.flask.ale_id.ale_experiment.project_id,
            'url': f"{_BASE_SEARCH_URL}?hidden_columns=&gene={quote(url_gene)}&min_freq=&max_freq=&ref_seq=&min_pos=&max_pos=&mut_type=&project=&strain={quote(strain or '')}",
        }

        exp = getattr(m, 'experiment', None)
        if isinstance(exp, dict):
            item['experiment'] = {
                'ale_experiment_id': exp.get('ale_experiment_id', m.sequencing_experiment.ale_experiment.ale_id),
                'sequencing_experiment_id': exp.get('sequencing_experiment_id', m.sequencing_experiment.id),
                'sample_name': exp.get('name'),
                'genotype': exp.get('type'),
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
        sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment__project__is_public=True
    )

    observed_mutations = []
    for item in ids:
        if not item:
            continue
        q = q_builder(item)
        if q is None:
            continue

        qs = ObservedMutation.objects.filter(public_project_q & q)
        mutations = filter_observed_mutations(qs, view_filter=view_filter)
        logger.info("Found %d mutations for %s", len(mutations), item, extra=user_extra(request))
        observed_mutations.extend(mutations)

    if not observed_mutations:
        return JsonResponse({'mutations': [], 'count': 0, 'message': invalid_msg})

    reseq_dict = collections.OrderedDict({obs_mut.sequencing_experiment.id: obs_mut.sequencing_experiment
                                            for obs_mut in observed_mutations})
    
    ale_experiment_ids = set()

    for observed_mutation in observed_mutations:
        ale_experiment_id = observed_mutation.sequencing_experiment.ale_experiment.ale_id
        logging.info("Processing mutation with ID: %s", ale_experiment_id, extra=user_extra(request))
        ale_experiment_ids.add(ale_experiment_id)
        if observed_mutation.sequencing_experiment_id in reseq_dict.keys():
            sample_name = reseq_dict[observed_mutation.sequencing_experiment_id].exp_ale_flask_isolate_str
            # Initialised here, and not only inside the branch below: it used to be assigned
            # nowhere else, so a row that failed the test either raised NameError or silently
            # reported the *previous* row's frequency.
            sample_type = ""
            if observed_mutation.present:
                sample_type = ("%2f" % float(observed_mutation.frequency)
                               if observed_mutation.frequency is not None else "")
            observed_mutation.experiment = {
                'ale_experiment_id': observed_mutation.sequencing_experiment.ale_experiment.ale_id,
                'sequencing_experiment_id': observed_mutation.sequencing_experiment.id,
                'name': sample_name,
                'type': sample_type
            }

    metadata = []

    for ale_experiment_id in ale_experiment_ids:
        logging.info("Processing reseq experiment with ID: %s", ale_experiment_id, extra=user_extra(request))
        experiment = AleExperiment.objects.get(ale_id=ale_experiment_id)
        if experiment:
            reseq_queryset = get_ordered_reseq_queryset(ale_experiment_id, None)
            reseq_info_list = get_reseq_info_list(reseq_queryset)
            experiment_info = {
                "reseq_info_list": reseq_info_list,
                "ale_experiment_name": experiment.name,
                "ale_project_name": experiment.project.name,
                "ale_project_id": experiment.project.id,
                "multiple": False,
                "ale_experiment_id": ale_experiment_id
            }
            metadata.append(experiment_info)
    
    mutations_data = _serialize_mutations(observed_mutations, search_gene=search_gene)
    experiment_metadata = _serialize_metadata(metadata)

    return JsonResponse({'mutations': mutations_data, 'experiment_metadata': experiment_metadata, 'count': len(mutations_data), 'message': 'Success'})




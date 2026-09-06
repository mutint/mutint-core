import time

from django.http import HttpResponse
from django.template import loader
from django.shortcuts import render
from mutint_sample.models import MutationCall
from django.db.models import Q
import operator
from functools import reduce
from mutint_sample.mutation_matrix import build_matrix
from mutint_experiment.utils import get_user_projects, get_strains
from mutint_sample.util import samples_in_calls, get_ref_sequences
from mutint_experiment.ancestor import exclude_all_ancestry
from mutint_filter.util import filter_mutation_calls
from mutint_common.util import get_user_context

from mutint_common.logger import user_extra, join_extras
import logging
from mutint_experiment import paths

logger = logging.getLogger(__name__)

MUT_TYPES = ['AMP', 'CON', 'DEL', 'INS', 'INV', 'MOB', 'SNP', 'SUB']
MUT_TYPES_DISPLAY = ["Amplification", "Conversion", "Deletion", "Insersion", "Inversion", "Mobil", "SNP", "Substitution"]

# These functions lazily load strains and reference sequences,
# ensuring that module-level imports do not trigger premature DB access 
# e.g., during test setup or before migrations.
def load_strains():
    return get_strains()
def load_ref_sequences():
    return get_ref_sequences()


def search(request):
    logger.info("search usage", extra=user_extra(request))
    try:
        context = get_user_context(request.user)
        user_projects = get_user_projects(request.user)
        context.update({"mut_types": MUT_TYPES,
                        "strains": load_strains,
                        "ref_seqs": load_ref_sequences,
                        "projects": user_projects})
        template = loader.get_template("search/search.html")

        if not request.GET:
            return render(request, 'search/search.html', context)

        start_time = time.time()
        last_search = _get_last_search(request)
        context.update({"last_search": last_search})

        search_include_param_list, search_exclude_param_list, message = _get_search_params(request, user_projects)

        if len(search_include_param_list) == 0 or (message and len(message) > 0):
            context.update({'message': message})
            return render(request, 'search/search.html', context)

        mutation_calls = _get_mutation_calls(search_include_param_list, search_exclude_param_list)
        # Samples from many experiments, so their labels carry the experiment's name, and
        # there is no one experiment for the matrix to remember a sample selection against.
        sample_dict = samples_in_calls(mutation_calls)
        matrix = build_matrix(mutation_calls, sample_dict, labels="qualified",
                              csv_title="search_results")

        context.update({"matrix": matrix,
                        "title": "Search Results",
                        "mutation_count": len(matrix.rows),
                        "mutation_call_count": len(mutation_calls),
                        })
        logger.info("search performance", extra=join_extras(
            {"parameters": last_search},
            {"time taken": time.time() - start_time}))
        return HttpResponse(template.render(context, request), content_type="text/html")
    except Exception as e:
        logger.exception("search broke", extra = user_extra(request))
        template = loader.get_template("500.html")
        context['err_message'] = str(e)
        return HttpResponse(template.render(context, request), content_type="text/html")


def _get_mutation_calls(search_include_param_list, search_exclude_param_list):
    """
    :param request:
    :return: mutation_queryset and mutation_call_queryset based on user request and user permission
    """
    call_qryset = _get_mut_qryset(search_include_param_list, search_exclude_param_list)
    # Unfiltered, and deliberately. Search spans experiments and a reader's filter belongs to
    # one, so there is no single value to apply; the page's own frequency boxes are its filter.
    # It used to apply each experiment's shared row, which is what its summary line said.
    mutation_calls = filter_mutation_calls(call_qryset)
    return mutation_calls


def _get_last_search(request):
    last_search = {}
    if request.GET['project']:
        project = int(request.GET['project'])
    else:
        project = ''
    if request.GET:
        last_search = {
            'gene': request.GET['gene'],
            'min_freq': request.GET['min_freq'],
            'max_freq': request.GET['max_freq'],
            'min_pos': request.GET['min_pos'],
            'max_pos': request.GET['max_pos'],
            'mut_type': request.GET['mut_type'],
            'project': project,
            'strain': request.GET['strain'],
            'ref_seq': request.GET['ref_seq']
        }
    return last_search


def _get_search_params(request, user_projects):
    include_argument_list = []
    exclude_argument_list = []
    message, small_range = _add_position_to_query(request, include_argument_list)
    if not message:
        message = _add_freq_to_query(request, include_argument_list)
    if not message:
        has_gene = _add_genes_to_query(request, include_argument_list, exclude_argument_list)
        _add_mutation_change_to_query(request, include_argument_list)
        has_refseq = _add_ref_seq_to_query(request, include_argument_list)
        has_strain =_add_strain_to_query(request, include_argument_list)
        has_project = _add_project_to_query(request, include_argument_list, user_projects)
        if not has_project and not has_gene and not has_strain and not has_refseq and not small_range:
            message = 'Please enter search criteria - genetic target, project or ref sequence or strain'
    return include_argument_list, exclude_argument_list, message


def _add_genes_to_query(request, include_argument_list, exclude_argument_list):
    """Turn the gene box into Q objects: `thrA`, `thr*`, `*A`, and `-` to exclude.

    Every lookup here is the case-insensitive variant deliberately. SQLite folds ASCII case in
    LIKE and PostgreSQL does not, so a plain `__contains` would answer differently on the two --
    and gene names are case-significant biology that people type freely, so `thra` finding `thrA`
    is the behavior this box has always had.

    The exclude branches are the sharp end, because they invert the harm: a case-sensitive
    negative search stops excluding and quietly returns *more* rows than asked for, which reads
    as the filter having been ignored rather than as a search returning nothing.
    """
    has_gene = False
    if 'gene' in request.GET:
        gene_list = request.GET['gene'].replace(" ", "").split(',')
        for mutated_gene in gene_list:
            if mutated_gene == '':
                continue
            if str(mutated_gene).startswith("-"):
                if str(mutated_gene).endswith("*"):
                    exclude_argument_list.append(Q(**{'mutation__gene__istartswith': str(mutated_gene)[1:-1]}))

                elif str(mutated_gene)[1:].startswith("*"):
                    exclude_argument_list.append(Q(**{'mutation__gene__iendswith': str(mutated_gene)[2:]}))

                else:
                    exclude_argument_list.append(Q(**{'mutation__gene__icontains': str(mutated_gene)[1:]}))
            else:
                if str(mutated_gene).endswith("*"):
                    include_argument_list.append(Q(**{'mutation__gene__istartswith': str(mutated_gene)[:-1]}))

                elif str(mutated_gene).startswith("*"):
                    include_argument_list.append(Q(**{'mutation__gene__iendswith': str(mutated_gene)[1:]}))
                else:
                    include_argument_list.append(Q(**{'mutation__gene__icontains': str(mutated_gene)}))
                has_gene = True
    return has_gene


def _add_position_to_query(request, include_argument_list):
    min, max = None, None
    small_range = False
    msg = ''
    try:
        if 'min_pos' in request.GET and len(request.GET['min_pos'])>0:
            min = int(request.GET['min_pos'].replace(',', ''))
        if 'max_pos' in request.GET and len(request.GET['max_pos'])>0:
            max = int(request.GET['max_pos'].replace(',', ''))
        if min and max:
            if min > max:
                msg= 'Invalid position: min > max'
            else:
                small_range = max - max < 1000
        if min:
            include_argument_list.append(Q(**{'mutation__position__gte': min}))
        if max:
            include_argument_list.append(Q(**{'mutation__position__lte': max}))
    except Exception as ex:
        msg = "Invalid positions. Please enter an integer!"
    return msg, small_range


def _add_freq_to_query(request, include_argument_list):
    min, max = None, None
    try:
        if 'min_freq' in request.GET and len(request.GET['min_freq'])>0:
            min = float(request.GET['min_freq'])
        if 'max_freq' in request.GET and len(request.GET['max_freq'])>0:
            max = float(request.GET['max_freq'])
        if min and max and min > max:
            return 'Invalid frequency: min > max'
        if min:
            include_argument_list.append(Q(**{'frequency__gte': min}))
        if max:
            include_argument_list.append(Q(**{'frequency__lte': max}))
    except Exception as ex:
        return "Invalid frequency. Please enter a number!"


def _add_mutation_change_to_query(request, include_argument_list):
    mut_type = request.GET['mut_type']
    if mut_type and len(mut_type) > 0:
        include_argument_list.append(Q(mutation__mutation_type=str(mut_type).upper()))


def _add_project_to_query(request, include_argument_list, user_projects):
    """
    :param request:
    :param include_argument_list:
    :param user_projects:
    :return: True if there is project param and the project is valid, else FALSE
    """
    # One evaluation of the queryset, not one per branch: `get_user_projects` returns a
    # QuerySet, and both branches below used to re-run it.
    project_ids = list(user_projects.values_list("id", flat=True))
    if request.GET['project']:
        project_id = request.GET['project']
        ok = int(project_id) in project_ids
        if ok:
            include_argument_list.append(Q(**{paths.to_experiment(paths.FROM_CALL, 'project_id'): project_id}))
        return ok
    elif not request.user.is_superuser:
        include_argument_list.append(
            Q(**{paths.to_experiment(paths.FROM_CALL, 'project_id__in'): project_ids}))
    return False


def _add_strain_to_query(request, include_argument_list):
    strain = request.GET['strain']
    if strain and len(strain) > 0:
        include_argument_list.append(Q(**{paths.to_population(paths.FROM_CALL, 'strain'): strain}))
        return True
    return False


def _add_ref_seq_to_query(request, include_argument_list):
    ref_seq = request.GET['ref_seq']
    if ref_seq and len(ref_seq)>0:
        include_argument_list.append(Q(mutation__seq_id=ref_seq))
        return True
    return False


def _get_mut_qryset(include_argument_list, exclude_argument_list):
    include_argument_list = reduce(operator.and_, include_argument_list)
    if len(exclude_argument_list) > 0:
        exclude_argument_list = reduce(operator.or_, exclude_argument_list)
        mut_qryset = MutationCall.objects.filter(include_argument_list).exclude(exclude_argument_list)
    else:
        mut_qryset = MutationCall.objects.filter(include_argument_list)

    # Designated ancestors are subtracted, across every experiment at once. Search is the one
    # page with no single experiment to name, so it cannot say *which* ancestor -- but showing
    # rows here that every experiment page excludes would make the two contradict each other,
    # and the reader has no way to tell which is answering their question. The summary line
    # says so in `own_rules`.
    return exclude_all_ancestry(mut_qryset)


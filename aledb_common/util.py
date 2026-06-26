import subprocess
import logging

from aledb_experiment.models import TechnicalReplicate
from django.core.cache import cache

__author__ = 'Patrick Phaneuf, Denny Gosting'

logger = logging.getLogger(__name__)

def clear_dashboard_cache():

    cache.delete('dashboard_mutation')

    cache.delete('dashboard_observed_mutation')

    cache.delete('dashboard_bar_chart_gene_dict')


def get_git_hash():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD']).rstrip()

try:
    common_context = {"git_hash": get_git_hash()}
except Exception:
    logger.exception("common_context broke")


def get_user_context(user):
    context = common_context.copy()
    # context.update({"experiments": get_all_user_exps(user)})
    return context


def is_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


# Gene annotation parsing — used across aledb_seq, aledb_filter, aledb_converge, aledb_stats
GENE_RANGE_ANNOTATION_DELIMITER = ", "
INTRAGENIC_LEFT_CHAR = ']'
INTRAGENIC_RIGHT_CHAR = '['


def get_gene_list(annotated_gene_list_str):
    """Parse a breseq gene annotation string into a clean list of gene names."""
    annotated_gene_list = annotated_gene_list_str.split(GENE_RANGE_ANNOTATION_DELIMITER)
    clean_gene_list = []
    for gene in annotated_gene_list:
        gene = gene.replace(INTRAGENIC_LEFT_CHAR, '').replace(INTRAGENIC_RIGHT_CHAR, '')
        clean_gene_list.append(gene)
    return clean_gene_list

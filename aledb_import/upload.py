import json
import re
from bs4 import BeautifulSoup
from aledb_import.gdparse.gdparse import gdparse
from aledb_common.util import _find_between
import collections
import numbers
from aledb_seq.models import Mutation, \
    ObservedMutation, \
    UnassignedMissingCoverageEvidence, \
    ResequencingExperiment
import os
from aledb_import.gene_annotation import get_annotated_gene_list
from aledb_filter.models import AleExperimentFilter
import aledb_filter.models
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

# breseq writes this beside output/, not inside it.
SUMMARY_JSON_RELATIVE_PATH = os.path.join("data", "summary.json")
GD_MUT_POS_ATTR_KEY = 'position'
GD_SIZE_TYPES = ['SUB', 'DEL', 'INV', 'INT', 'AMP', 'CON']
GD_CNV_LENGTH_ATTR_KEY = 'size'
GD_MUT_GENE_NAME_ATTR_KEY = 'gene_name'
GD_MUT_GENE_PRODUCT_ATTR_KEY = 'gene_product'  # Will contain list of genes for mutations affecting many.
GD_MUT_TYPE_ATTR_KEY = 'type'
GD_MUT_FREQ_ATTR_KEY = 'frequency'
GATK_MUT_FREQ_ATTR_KEY = ''
GD_MUT_HTML = 'html_mutation'
GD_MUT_ANNOTATION_HTML = "html_mutation_annotation"
GD_MUT_SEQ_ID_ATTR_KEY = 'seq_id'
DEFAULT_CLONAL_FREQ = 1.0
DEFAULT_GATK_FREQ = 0
BRESEQ_RESULT_RELATIVE_PATH = ""


def add_breseq_results(technical_replicate_id,
                       person,
                       experiment_path,
                       mutation_gd_parser,
                       reseq_ref_name,
                       sample_name,
                       experiment=None,
                       is_wild_type=False):
    """Import one sample's mutations, missing-coverage evidence and statistics.

    The clonal/population distinction used to be read out of the GenomeDiff here and passed
    down, purely to pick which CSS classes to scrape out of index.html. With the HTML gone
    it has no remaining use on this path.
    """
    reseq = _get_reseq_experiment_with_stats(experiment_path,
                                             sample_name,
                                             technical_replicate_id,
                                             person)

    sample_mutation_dict = mutation_gd_parser.data[gdparse.MUTATION_KEY]

    _database_mutations(experiment_path,
                        sample_name,
                        reseq,
                        sample_mutation_dict,
                        experiment,
                        is_wild_type)

    sample_evidence_dict = mutation_gd_parser.data[gdparse.EVIDENCE_KEY]

    _database_unassigned_missing_coverage(reseq, sample_evidence_dict)


def _is_missing_coverage_type(evidence_dict):
    is_missing_coverage = False
    if evidence_dict[gdparse.EVIDENCE_TYPE_KEY] == gdparse.MISSING_COVERAGE_EVIDENCE_TYPE:
        is_missing_coverage = True
    return is_missing_coverage


# Should be able to re-use this with populations.
def _database_unassigned_missing_coverage(seq_experiment, evidence_dict):
    """Record MC evidence from the GenomeDiff.

    This used to also scrape index.html for reads_left_url / coverage / size / gene /
    description and attach them to the row. Nothing ever read those columns, and they are
    gone along with the rest of the breseq HTML report support.
    """
    for key in evidence_dict:
        if _is_missing_coverage_type(evidence_dict[key]):
            UnassignedMissingCoverageEvidence.objects.get_or_create(
                seq_id=evidence_dict[key]['seq_id'],
                start=evidence_dict[key]['start'],
                end=evidence_dict[key]['end'],
                sequencing_experiment=seq_experiment)


def _read_breseq_summary(sample_root):
    """The four sample statistics from breseq's ``data/summary.json``, or None if absent.

    Replaces scraping them out of ``summary.html`` with BeautifulSoup. The JSON carries the
    same numbers as real values, so there is no table-index guessing and no dependence on the
    HTML report existing at all.

    Note the location: ``<sample>/data/summary.json``, a sibling of ``output/`` -- not inside
    it, which is where the HTML lived.
    """
    path = os.path.join(sample_root, SUMMARY_JSON_RELATIVE_PATH)
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            summary = json.load(handle)
    except (ValueError, OSError):
        # Logged rather than swallowed: the old scrape had a bare `except: None` that left
        # the statistics silently at zero.
        logger.exception("could not read breseq summary %s", path)
        return None

    reads = summary.get("reads") or {}
    total_reads = reads.get("total_reads") or 0
    total_bases = reads.get("total_bases") or 0

    return {
        "reads": total_reads,
        "average_read_length": (total_bases / total_reads) if total_reads else 0,
        "percentage_mapped": (reads.get("total_fraction_aligned_reads") or 0) * 100,
        "mean_coverage": _mean_coverage(summary),
    }


def _mean_coverage(summary):
    """Length-weighted mean coverage across the reference sequences.

    One reference -- the usual case -- reduces to exactly that reference's
    ``coverage_average``. Junction-only entries are not real sequence and are skipped.
    """
    references = ((summary.get("references") or {}).get("reference") or {})

    weighted = 0.0
    total_length = 0
    for reference in references.values():
        if reference.get("junction_only"):
            continue
        length = reference.get("length") or 0
        coverage = reference.get("coverage_average") or 0
        weighted += coverage * length
        total_length += length

    return (weighted / total_length) if total_length else 0


def _get_reseq_experiment_with_stats(experiment_path, sample_name, technical_replicate_id, person):
    """Get or create the sample's row and fill in its sequencing statistics.

    The row used to be keyed on three stored paths as well -- `location`, `gatk_location`,
    `experiment_location` -- which meant it forked a duplicate whenever a path changed, e.g.
    when the breseq HTML report appeared or ALE_DATA_ROOT_DIR moved. Keying on
    (sample_name, tech_rep, person) matches what the web importer already does and gives one
    row per technical replicate, which is the intended meaning.
    """
    reseq, created = ResequencingExperiment.objects.get_or_create(
        sample_name=sample_name,
        tech_rep_id=technical_replicate_id,
        person=person)

    statistics = _read_breseq_summary('%s/breseq/%s' % (experiment_path, sample_name))
    if statistics:
        for field, value in statistics.items():
            setattr(reseq, field, value)

    reseq.save()
    return reseq


def _database_mutations(experiment_path,
                        sample_name,
                        seq_experiment,
                        mutation_dict,
                        experiment,
                        is_wild_type):

    observed_mutation_list = []

    # Only used if is_wild_type is True. Doesn't affect functionality otherwise
    # TODO: if this is the case, needs a conditional so not always executed.
    wild_type_mutation_list = []

    for mut_num in sorted(mutation_dict.keys()):
        breseq_gene_annotation = mutation_dict[mut_num].get(GD_MUT_GENE_NAME_ATTR_KEY)
        breseq_gene_product_annotation = mutation_dict[mut_num].get(GD_MUT_GENE_PRODUCT_ATTR_KEY)
        gene_list = get_annotated_gene_list(breseq_gene_annotation, breseq_gene_product_annotation)
        gene_list_str = ', '.join(gene_list)
        sequence_change_str, protein_change_str = "", ""
        if GD_MUT_HTML in mutation_dict[mut_num].keys():
            # What is in the mutation HTML under the "mutation" column.
            bs_html = BeautifulSoup(mutation_dict[mut_num].get(GD_MUT_HTML), "lxml")
            sequence_change_str = bs_html.text.replace(u'\xa0', u' ').strip()
        size = None
        if mutation_dict[mut_num].get(GD_MUT_TYPE_ATTR_KEY) in GD_SIZE_TYPES:
            size = mutation_dict[mut_num].get(GD_CNV_LENGTH_ATTR_KEY)
        if GD_MUT_ANNOTATION_HTML in mutation_dict[mut_num].keys():
            # What is in the mutation HTML under the "annotation" column.
            bs_html = BeautifulSoup(mutation_dict[mut_num].get(GD_MUT_ANNOTATION_HTML), "lxml")
            protein_change_str = bs_html.text.replace(u'\xa0', u' ').strip()

        mut, \
        created = Mutation.objects.get_or_create(position=mutation_dict[mut_num].get(GD_MUT_POS_ATTR_KEY),
                                                 gene=gene_list_str,
                                                 reseq_reference=mutation_dict[mut_num].get(GD_MUT_SEQ_ID_ATTR_KEY),
                                                 product=breseq_gene_product_annotation,
                                                 feature_length=size,
                                                 sequence_change=sequence_change_str,
                                                 mutation_type=mutation_dict[mut_num].get(GD_MUT_TYPE_ATTR_KEY),
                                                 protein_change=protein_change_str)
        mut.save()
        if is_wild_type is True:
            wild_type_mutation_list.append(mut.id)

        frequencies = _get_mutation_freq(mutation_dict[mut_num])

        # `evidence` and `gatk_evidence` used to be filled in here -- the former scraped out
        # of index.html, the latter a filename guess. Neither was ever read back, and both
        # columns are gone with the rest of the breseq HTML report support.
        observed_mutation = ObservedMutation(sequencing_experiment=seq_experiment,
                                             mutation=mut,
                                             breseq_present=True,
                                             gatk_present=True,
                                             frequency=frequencies[0],
                                             frequency_gatk=frequencies[1])
        observed_mutation_list.append(observed_mutation)

    ObservedMutation.objects.bulk_create(observed_mutation_list)

    if is_wild_type is True:
        exp_filter, created = AleExperimentFilter.objects.get_or_create(
            ale_experiment=experiment,
            defaults=aledb_filter.models.get_default_experiment_filter_params(experiment))
        exp_filter.starting_strain_mutations = ','.join(str(mut) for mut in wild_type_mutation_list)
        exp_filter.save()


def _get_mutation_freq(mutation_dict):
    frequency = DEFAULT_CLONAL_FREQ
    frequency_gatk = DEFAULT_GATK_FREQ

    for key in mutation_dict.keys():
        if key.startswith('frequency_'):
            if key.endswith('breseq') or key.endswith('output'):
                if isinstance(mutation_dict[key], float) or isinstance(mutation_dict[key], int):
                    frequency = mutation_dict[key]
                else:
                    frequency = 0
            elif key.endswith('GATK_CNVnator'):
                if isinstance(mutation_dict[key], float) or isinstance(mutation_dict[key], int):
                    frequency_gatk = mutation_dict[key]
                else:
                    frequency_gatk = 0

    return [frequency, frequency_gatk]

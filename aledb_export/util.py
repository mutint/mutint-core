from aledb_common.plugin_registry import get_export_handler
from aledb_seq.views.mutation_table_builder import get_mutation_table_data, HTML_MUTATION_TABLE_HEADER
from django.utils.html import strip_tags

from aledb_filter.util import filter_mutation_calls
from aledb_seq.util import get_mutation_call_queryset, get_ordered_reseq_dict

MUT_TYPE_STR = "mut"


def get_csv_str(exp_id, mut_type_str, view_filter=None):
    """One experiment's rows for the CSV, through the reader's filter.

    `view_filter` is resolved per experiment by the view, so a multi-experiment zip carries each
    experiment's own filter and each file is what that experiment's page showed.

    A pre-existing divergence, left alone deliberately: no `filter_type` is passed here, so an
    export includes AMP rows where the fixation and converge *pages* exclude them --
    `get_table_body` hardcodes `filter_type='AMP'`. Changing it would silently change every
    export anyone has ever taken, so it is recorded rather than fixed.

    **The `mut` export does not subtract the designated ancestor**, which is why this reaches
    for `get_mutation_call_queryset` rather than the `get_evolved_*` sibling every
    analysis uses. A download called "all mutations" that quietly dropped a sample and a set
    of rows would be a worse answer than a faithful one; a reader who wants the subtracted set
    takes it from the page that shows it. The columns follow for free -- they are built from
    `get_ordered_reseq_dict(mutation_calls)`, i.e. from the rows themselves, so the
    ancestor's column comes back with its rows and nothing here special-cases it.

    A *derived* export is a different question and answers it differently. `fixed_mut` and
    `converged_mut` come from `get_export_handler`, and those sets are computed with the
    ancestor already subtracted -- there is no un-subtracted version of "what converged". So
    the rule is: the raw export is what is stored, and a derived export is exactly what its
    page showed.
    """
    if mut_type_str == MUT_TYPE_STR:
        call_qryset = get_mutation_call_queryset(exp_id)
    else:
        handler = get_export_handler(mut_type_str)
        if handler is None:
            return []
        call_qryset = handler(exp_id, view_filter)

    mutation_calls = filter_mutation_calls(call_qryset, view_filter=view_filter)
    reseq_ordered_dict = get_ordered_reseq_dict(mutation_calls)

    mutations, table_entry_list, mutation_index_dict = get_mutation_table_data(reseq_ordered_dict, mutation_calls)

    # Where the mutation's own columns start in HTML_MUTATION_TABLE_HEADER. Was 3, and
    # moved with the close-icon column that used to sit at index 0.
    mut_pos_index = 2
    rows = [
        HTML_MUTATION_TABLE_HEADER[mut_pos_index:] + [reseq_ordered_dict[reseq].qualified_label for reseq in
                                                      reseq_ordered_dict]]

    rows += ([
            "" if mutation.reseq_reference is None else mutation.reseq_reference,
            format(mutation.position, ',d'),
            mutation.mutation_type,
            mutation.sequence_change,
            mutation.gene,
            "" if mutation.product is None else mutation.product,
            mutation.id,
            strip_tags(mutation.protein_change)] + _strip_tags_from_list(
        table_entry_list[mutation_index_dict[mutation.id]])
             for mutation in mutations)
    return rows


def _strip_tags_from_list(frequencies):
    temp = []
    for frequency in frequencies:
        temp.append(strip_tags(frequency))
    return temp


from django.utils.html import strip_tags

from mutint_common.plugin_registry import get_export_handler
from mutint_filter.util import filter_mutation_calls
from mutint_sample.util import get_mutation_call_queryset, get_ordered_reseq_dict

MUT_TYPE_STR = "mut"

#: The CSV's own header, byte-identical to what the shared table's header used to lend it.
#: Kept as it was -- "Gene (Scrollable)" included -- because changing the header of a file
#: people have scripts reading is a different decision from changing a page.
CSV_MUTATION_HEADER = ["Reference Seq", "Position", "Mutation Type", "Sequence Change",
                       "Gene (Scrollable)", "Product", "Mut ID", "Details"]

#: A present call with no frequency -- a hand-added mutation. The character, where the table
#: this borrowed from wrote the HTML entity and `strip_tags` left it in the file verbatim.
PRESENT_MARK = "✓"


def get_csv_str(exp_id, mut_type_str, view_filter=None):
    """One experiment's rows for the CSV, through the reader's filter.

    `view_filter` is resolved per experiment by the view, so a multi-experiment zip carries each
    experiment's own filter and each file is what that experiment's page showed.

    A pre-existing divergence, left alone deliberately: no `filter_type` is passed here, so an
    export includes AMP rows where the fixation and converge *pages* exclude them. Changing it
    would silently change every export anyone has ever taken, so it is recorded rather than fixed.

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

    **Self-contained since the mutation matrix replaced the shared table.** This used to borrow
    the on-screen table's header and its cell markup and strip the tags back out; what is
    written now is the plain text those cells always meant. A row is included when at least one
    sample carries the mutation, as before.
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
    column_of = {sample_id: index for index, sample_id in enumerate(reseq_ordered_dict)}

    rows = [CSV_MUTATION_HEADER + [reseq_ordered_dict[reseq].qualified_label
                                   for reseq in reseq_ordered_dict]]

    # Every mutation any of the calls names gets a row, in first-appearance order, whatever its
    # cells say -- which is what the file has always held. (The on-screen matrix drops a row
    # no listed sample carries; the export never did, and a change there is a change to every
    # file anyone has scripted against.)
    mutations = {}
    cells = {}
    for call in mutation_calls:
        mutations.setdefault(call.mutation_id, call.mutation)
        row_cells = cells.setdefault(call.mutation_id, [""] * len(column_of))
        if call.sample_id in column_of:
            row_cells[column_of[call.sample_id]] = _sample_cell(call)

    for mutation_id, mutation in mutations.items():
        rows.append([
            "" if mutation.seq_id is None else mutation.seq_id,
            format(mutation.start_position, ',d'),
            mutation.mutation_type,
            mutation.sequence_change,
            mutation.gene,
            "" if mutation.product is None else mutation.product,
            mutation.id,
            strip_tags(mutation.protein_change or "")] + cells[mutation_id])
    return rows


def _sample_cell(call):
    """What one sample's cell says: the frequency, the mark, a read-support pair, or nothing."""
    if call.present:
        return ("%.2f" % float(call.frequency)) if call.frequency is not None else PRESENT_MARK
    if call.present is False:
        evidence = call.evidence or {}
        mutated, wt = evidence.get("mutated_reads"), evidence.get("wt_reads")
        if mutated is None or wt is None:
            return ""
        return "%s/%s" % (mutated, wt)
    return ""


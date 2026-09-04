from django.contrib.auth.models import User
import re
from django.urls import reverse
from django.utils.html import strip_tags
from aledb_sample.util import get_ecocyc_gene_list
from aledb_filter.util import filter_mutation_calls
from aledb_common.util import GENE_LIST_LIMIT, get_gene_list
from aledb_common.constants import TAGS, ROW_TAGS, COLUMN_TAGS, HTML_MUTATION_TABLE_HEADER
from aledb_experiment.models import Experiment
from aledb_experiment.permissions import can_curate
from aledb_sample.ncbi import verified_contig_names


HTML_EMPTY_MUTATION_CELL = """<span class="empty"></span>"""
#: A call that says the mutation was looked for and *not* found. The counts are how many
#: reads supported each answer, when the caller recorded them; `evidence` is where they live
#: now and `.get` is why an absent pair renders the marker alone rather than raising. It used
#: to read two columns straight into `%d` -- and since no import path ever wrote them, the
#: first `present=False` row to reach a table would have been a TypeError, not a cell.
HTML_MUTATION_ABSENT_CELL = """<span class="false">%s</span>"""

EXPANDABLE_COLUMN_PLUS_SIGN = """<i onclick="expand_collapse_gene_entry(this)" class="fa fa-plus pull-left" aria-hidden="true" data-toggle="collapse" data-target="#%s"></i>"""
EXPANDABLE_GENE_ENTRY = """<div class="collapse pull-left" id="%s">%s</div>"""
GENE_CELL_WRAPPER = """<div style="width: 150px; white-space: nowrap; overflow-x: scroll;">%s</div>"""
non_decimal = re.compile(r'[^\d.]+')
evidence = re.compile(r'[A-Z]\d+[A-Z]')
REP_DROPDOWN = '<div class="dropdown tag_dropdown"><button class="btn btn-default btn-xs dropdown-toggle" type="button" id="dropdownMenu2" data-toggle="dropdown" aria-haspopup="true" aria-expanded="true">' \
             '<i class="fa fa-tags" aria-hidden="true"></i>' \
             '</button>' \
             '<ul class="dropdown-menu" aria-labelledby="dropdownMenu1">' \
             '%s' \
             '</ul>'
REP_TAG = '</div><div class="tag_dropdown">%s</div>'

_table_cell_dropdown_template = """<div class="dropdown">
  <button class="btn btn-default btn-xs dropdown-toggle" type="button" id="dropdownMenu1" data-toggle="dropdown" aria-haspopup="true" aria-expanded="true">
    <i class="fa fa-bars" aria-hidden="true"></i>
  </button>
  <ul class="dropdown-menu" aria-labelledby="dropdownMenu1">
    %s
  </ul>
</div>"""


def _build_table_cell_for_dropdown(mutation, experiment):
    """Returns a <div> element containing the drop down menu for each row
    in the mutation table.

    NOTE: this is a temporary solution for implementing #425 until the
    mutation table component rendering is refactored

    "Save to Experiment Filter" used to lead this menu, appending the mutation's id to
    `AleExperimentFilter.ignored_mutations` so it would stop being shown. Removing a mutation
    is `aledb_mutation_editor`'s job now, so what is left here is tagging.
    """
    return _table_cell_dropdown_template % _get_tag_filter_dropdown_entries(mutation.id)


def get_table_header(user, reseq_dict, experiment: Experiment = None):
    base_table_header = HTML_MUTATION_TABLE_HEADER
    table_header_list = []

    for sample_id in reseq_dict:
        reseq = reseq_dict[sample_id]
        sample_name = reseq.label
        if not experiment:
            sample_name = reseq.qualified_label

        # Plain text: this used to link to <experiment_location>/<sample>.html, a breseq
        # report page that no longer exists.
        sample_header_html = sample_name
        if can_curate(user, experiment):
            dropdown_html = _get_sample_tag_dropdown_entries(reseq)
            sample_header_html += (REP_DROPDOWN % dropdown_html)
        current_tags = _get_sample_tags(reseq)
        sample_header_html += (REP_TAG % current_tags)
        table_header_list.append(sample_header_html)
    return base_table_header + table_header_list


def get_mutation_table_body(user: User, mutation_calls: [], reseq_dict, experiment: Experiment = None, is_gene_table: bool = False):
    mutations, table_entry_list, mutation_index_dict = get_mutation_table_data(reseq_dict, mutation_calls)

    # Resolved once for the whole table rather than per row: an experiment's table runs to
    # hundreds of mutations and they all share a handful of contigs.
    verified_contigs = verified_contig_names(experiment)

    protein_changes = {}
    table_body = []
    for mutation in mutations:
        if _contains_mutation(table_entry_list[mutation_index_dict[mutation.id]]):
            table_row = []
            if can_curate(user, experiment):
                table_row.append(_build_table_cell_for_dropdown(mutation, experiment))
            else:
                table_row.append("""""")

            table_row.append(_get_mutation_tags(mutation.tags))
            table_row.append(_refseq_cell(mutation, verified_contigs))
            table_row.append(format(mutation.position, ',d'))
            table_row.append(mutation.mutation_type)
            table_row.append(mutation.sequence_change)
            table_row.append(get_gene_table_entry(mutation))
            table_row.append("" if mutation.product is None else mutation.product)
            table_row.append(mutation.id)
            if is_gene_table:
                if evidence.search(mutation.protein_change):
                    try:
                        table_row.append("<a id=\"%s\" onclick=\"highlight_mutation(%d,%d)\" target=\"_blank\">%s</a>" %
                                         ("mutation_" + str(mutation.id),
                                          int(non_decimal.sub('', mutation.protein_change)),
                                          int(mutation.id), mutation.protein_change))
                        protein_changes[mutation.id] = strip_tags(mutation.protein_change)
                    except:
                        table_row.append(mutation.protein_change)
                else:
                    table_row.append(mutation.protein_change)
            else:
                table_row.append(mutation.protein_change)
            table_row += table_entry_list[mutation_index_dict[mutation.id]]

            table_body.append(table_row)

    if is_gene_table:
        return table_body, protein_changes

    return table_body


def get_mutation_table_data(reseq_dict, mutation_calls):
    mutation_map = {call.mutation.id: call.mutation for call in mutation_calls}
    mutation_index_dict = dict((mutation_id, i) for i, mutation_id in enumerate(mutation_map.keys()))
    experiment_id_idx_mapping_dict = _get_experiment_id_idx_mapping_dict(reseq_dict)
    # Initialize all sample mutation table cells as empty.
    table_entry_list = _initialize_table(experiment_id_idx_mapping_dict, mutation_index_dict)
    for mutation_call in mutation_calls:
        new_entry = _get_table_mutation_entry(mutation_call, reseq_dict)
        if new_entry is not None and mutation_call.sample_id in reseq_dict.keys():
            table_entry_list[mutation_index_dict[mutation_call.mutation_id]][
                experiment_id_idx_mapping_dict[mutation_call.sample_id]] = new_entry
    return mutation_map.values(), table_entry_list, mutation_index_dict


# reference to a seq_experiment that doesn't exist due to checkbox filtering.
# This makes this function very confusing.
def get_table_body(user: User,
                   reseq_dict,
                   mutation_call_queryset,
                   experiment=None,
                   is_gene_table=False,
                   *,
                   view_filter=None):
    """Render a queryset of calls as the shared mutation table's body.

    `filter_type='AMP'` means *exclude* AMP -- the values read backwards. Only the plugin tables
    (fixation, converge) reach this now, and they keep the old behaviour; /mutations deliberately
    no longer excludes AMP, since its dedicated page is gone.

    **`view_filter` defaults to None, and both callers pass None on purpose.** A reader's filter
    changes *which mutations fix and which converge* -- fixation asks what is present in an ALE's
    last two flasks, convergence asks which genes were hit in more than one ALE -- so it has to be
    applied before those questions are answered, not after the answer has been rendered. Both
    plugins apply it when they compute their set, and filtering the same rows again here would be
    a no-op in the good case and a source of drift in every other. Pass one only if you are
    rendering a queryset nobody has filtered yet.
    """
    mutation_calls = filter_mutation_calls(
        mutation_call_queryset, filter_type='AMP', view_filter=view_filter)
    return get_mutation_table_body(user, mutation_calls, reseq_dict, experiment, is_gene_table)


def get_gene_table_entry(mutation):
    """One Gene cell: the names in a fixed-width strip that scrolls sideways.

    Past ten genes the list is collapsed behind Bootstrap's own collapse, driven by the
    fa-plus icon -- `expand_collapse_gene_entry` in `table_template.js` only swaps the icon,
    the showing and hiding is `data-toggle`/`data-target`. This is not the Show button on the
    breseq-style table, which is different markup from `aledb_import.annotate.display` and has
    its own handler in `breseq_table.js`.

    **The wrapper is closed once, here, rather than inside each branch.** It went unclosed on
    the expandable branch for a long time with no visible effect, and the reason is worth
    knowing before treating the shape as free: `table_body` reaches the page as a JS literal
    and DataTables sets each cell as its own `<td>`'s innerHTML, so the parser closed the div
    at the end of the fragment and the DOM came out byte-identical (measured, both branches).
    A fragment that only parses correctly because of where it happens to be delivered is one
    change of delivery away from not, and the asymmetry reads as a bug every time somebody
    finds it.
    """
    names = get_gene_list(mutation.gene)

    # Past GENE_LIST_LIMIT the names are not rendered at all, and there is nothing to open.
    # The import path stops recording them at the same limit, so this is reached only by rows
    # written before it existed -- `aledb_sample.0012` moves the ones this database had. Counted
    # before `get_ecocyc_gene_list`, which wraps every name in an <a>: on an ecocyc reference
    # the widest mutation here would otherwise build a 0.36 MB cell, measured.
    if len(names) > GENE_LIST_LIMIT:
        return GENE_CELL_WRAPPER % ("%d genes" % len(names))

    cleaned_gene_list = get_ecocyc_gene_list(names, mutation.is_ecocyc_gene())
    joined = ", ".join(cleaned_gene_list)

    if len(cleaned_gene_list) > 10:
        body = (EXPANDABLE_COLUMN_PLUS_SIGN % str(mutation.id)
                + EXPANDABLE_GENE_ENTRY % (str(mutation.id), joined))
    else:
        body = joined

    return GENE_CELL_WRAPPER % body


def _initialize_table(experiment_id_idx_mapping, mutations):
    return [[HTML_EMPTY_MUTATION_CELL] * len(experiment_id_idx_mapping) for _ in range(len(mutations))]


def _get_experiment_id_idx_mapping_dict(seq_experiment_dict):
    experiment_id_idx_mapping = dict((reseq_exp_id, idx) for idx, reseq_exp_id in enumerate(seq_experiment_dict.keys()))
    return experiment_id_idx_mapping


def _get_table_mutation_entry(mutation_call, reseq_dict):
    """One mutation-table cell.

    The frequency links to the genome browser when the sample has a stored alignment, so a
    cell is the way in to the pileup at that position. `class="true"` is load-bearing and
    must survive on both branches: `_contains_mutation` below substring-tests it to decide
    whether a row renders at all, and table_template.js tests it to colour the cell.
    """
    table_entry = ""
    if mutation_call.present:
        # A call may carry no frequency at all -- a hand-added mutation need not
        # claim one -- so a tick rather than None formatted with %.2f.
        label = ("%.2f" % float(mutation_call.frequency)
                 if mutation_call.frequency is not None else "&#10003;")
        table_entry = _cell_html(mutation_call, reseq_dict, label)

    elif mutation_call.present is False:
        # Looked for and not found. `class="false"`, never `"true"`: `_contains_mutation`
        # substring-tests the row for the latter, so an absent call must not be what makes a
        # row render.
        table_entry = HTML_MUTATION_ABSENT_CELL % _read_support(mutation_call)

    return table_entry


def _read_support(mutation_call):
    """``<mutated>/<wt>`` when the caller recorded both, else nothing.

    Both halves or neither: one number over a blank says nothing a reader can act on, and a
    caller records the pair together or not at all.
    """
    evidence = mutation_call.evidence or {}
    mutated, wt = evidence.get("mutated_reads"), evidence.get("wt_reads")
    if mutated is None or wt is None:
        return ""
    return "%s/%s" % (mutated, wt)


def _cell_html(mutation_call, reseq_dict, label):
    """The cell's markup: a browser link when there is an alignment to show, else plain text.

    Only the breseq-folder importer stores a BAM, so a bare .gd or legacy CLI sample has none
    and gets no link -- linking would just send the user to a page explaining its absence.
    `reseq_dict` holds already-loaded rows, so `bam_stored` costs no query.
    """
    reseq = reseq_dict.get(mutation_call.sample_id)
    if reseq is None or not reseq.bam_stored:
        return """<span class="true">%s</span>""" % label

    return """<a class="true" href="%s?mutation_call_id=%d" title="View the pileup at this position">%s</a>""" % (
        reverse("browse_mutation"), mutation_call.id, label)


def _contains_mutation(filtered_mutation_calls_row):
    contains_mutation = False
    for mutation_call_entry in filtered_mutation_calls_row:
        if "true" in mutation_call_entry:
            contains_mutation = True
    return contains_mutation


def _get_mutation_tags(tags):
    html = ''
    if tags:
        for tag in tags.split(','):
            html += '<span class="fa-stack">%s<font style="font-size:0px">%s</font></span>' % (TAGS[tag], tag)
    return html


def _get_tag_filter_dropdown_entries(mutation_id):
    html = ''
    for tag in ROW_TAGS:
        html += '<li><a onclick="add_tag(\'%s\', %d, this)" style="cursor:pointer">Toggle Tag: %s %s</a></li>' % (
        tag, mutation_id, tag, TAGS[tag])

    return html


def _get_sample_tag_dropdown_entries(sample):
    dropdown_html = ''
    for tag in COLUMN_TAGS:
        image = TAGS[tag]
        dropdown_html += '<li><a onclick="add_tag_to_replicate(\'%s\', %d, this)">Toggle Tag: %s %s</a></li>' % (
        tag, sample.id, tag, image)
    return dropdown_html


def _get_sample_tags(sample):
    tags = sample.tags;
    current_tags = ''
    for tag in COLUMN_TAGS:
        image = TAGS[tag]
        if tags and tag in tags:
            current_tags += '<span class="fa-stack">%s<font style="font-size:0px">%s</font></span>' % (image, tag)
        else:
            current_tags += '<span class="fa-stack" style="display:none">%s<font style="font-size:0px">%s</font></span>' % (image, tag)
    return current_tags


def _refseq_cell(mutation, verified_contigs):
    """The Reference Seq cell: the contig name, linked into the NCBI viewer.

    **Every contig is linked, verified or not**, and that is a correction rather than the
    original intent. It first followed `_cell_html`'s rule -- do not link to a page that can
    only explain an absence -- but that rule fits a sample with no BAM, where the page is a
    dead end. This page is *actionable*: unverified, it names the contig and offers the box
    that records its accession. Gating the link on verification made the only page carrying
    that box reachable solely once the work it exists for was already done.

    Two things this markup must not do, both of which fail silently. It must not carry
    `class="true"`, and the string `true` must not appear in it at all: `_contains_mutation`
    substring-tests the row for that literal to decide whether the row renders, and
    `table_template.js` tests for it to colour a sample cell. And it must not add or remove a
    column -- everything in `table_template.js` is indexed relative to
    `REFSEQ_COLUMN_IN_MUT_TABLE`, and `aledb_export.util` re-derives this value itself rather
    than reusing this cell, so the CSV is unaffected by the anchor.
    """
    name = mutation.reseq_reference
    if name is None:
        return ""

    title = ("Show this position in the NCBI annotation" if name in verified_contigs
             else "This sequence has not been matched to an NCBI record yet")
    return """<a href="%s?mutation_id=%d" title="%s">%s</a>""" % (
        reverse("ncbi_view"), mutation.id, title, name)

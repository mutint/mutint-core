"""Rebuild breseq's mutation table for one resequencing sample.

The stored annotation is the same key set `gdtools ANNOTATE` writes, and the raw
.gd record sits beside it, so a row is just the two merged and handed to the
display layer. That is the whole reason annotation lives in its own JSON column
rather than being scattered across twenty scalar ones: rendering is a dict merge,
not a rebuild.

Mutations imported before a reference was available have no annotation to render
from. Those fall back to the flat columns the ordinary mutation table uses -- the
same information, without breseq's markup.
"""

from mutint_import.annotate.display import (add_html_fields, commify, gene_list_names,
                                             html_gene_list)

#: The gene-association fields, as `(key, title, italic)`: the genes a mutation is predicted
#: to inactivate, the genes it touches without that, and the genes whose promoter it sits in --
#: each as names and as locus tags, written into `Mutation.annotation` by the annotator exactly
#: as `gdtools ANNOTATE` does -- and then MutInt's own single gene and single locus tag
#: affected, the union of those three when it is one (`annotation.SINGLE_AFFECTED_FIELDS`).
#: These are the optional columns both mutation tables offer, hidden until asked for. `italic`
#: is whether the values are gene symbols, which breseq sets in italics, or locus tags, which it
#: does not.
GENE_LIST_FIELDS = (
    ("genes_inactivated", "Genes inactivated", True),
    ("genes_overlapping", "Genes overlapping", True),
    ("genes_promoter", "Genes promoter", True),
    ("locus_tags_inactivated", "Locus tags inactivated", False),
    ("locus_tags_overlapping", "Locus tags overlapping", False),
    ("locus_tags_promoter", "Locus tags promoter", False),
    ("single_gene_affected", "Single gene affected", True),
    ("single_locus_tag_affected", "Single locus tag affected", False),
)


def gene_list_text(annotation, key):
    """One gene-list field as plain text, `thrA, thrB`: what a CSV carries for it."""
    return ", ".join(gene_list_names((annotation or {}).get(key)))


def _gene_list_cells(annotation):
    """The gene-list cells for a row, each as HTML and as `<key>_text` for export."""
    cells = {}
    for key, _title, italic in GENE_LIST_FIELDS:
        cells[key] = html_gene_list((annotation or {}).get(key), italic=italic)
        cells[key + "_text"] = gene_list_text(annotation, key)
    return cells


def is_mixed(sample):
    """Whether to show breseq's Freq column for this sample.

    Here rather than on either view because both render the table and the answer must be the
    same in both -- a clonal sample has no frequency column on the Samples page and must not
    grow one on the browser page.

    It was `is_population` over a column of that name; both were inverted, and the question
    it answers did not change -- a *mixed* sample is the one with frequencies.
    """
    if sample is None:
        return False
    return bool(sample.is_mixed)


def gd_entry(mutation):
    """The annotated GD record for a Mutation, or None if it has no annotation.

    The record alone is not enough: a SNP's Mutation column is "ref→new", and the
    reference base is something annotation derives, not something the .gd carries.
    """
    if not mutation.genome_diff or not mutation.annotation:
        return None
    entry = dict(mutation.genome_diff)
    entry.update(mutation.annotation)
    return entry


def _annotated_row(entry):
    add_html_fields(entry)
    row = {
        "seq_id": entry.get("html_seq_id", ""),
        "position": entry.get("html_position", ""),
        "mutation": entry.get("html_mutation", ""),
        "annotation": entry.get("html_mutation_annotation", ""),
        "gene": entry.get("html_gene_name", ""),
        "description": entry.get("html_gene_product", ""),
        "annotated": True,
    }
    row.update(_gene_list_cells(entry))
    return row


def _plain_row(mutation):
    """A row for a mutation with no stored annotation.

    The gene lists are still read from `annotation`: a `.gd` that breseq had annotated, imported
    before the experiment had a reference, keeps whatever gene fields it carried there.
    """
    row = {
        "seq_id": mutation.seq_id or "",
        "position": commify(str(mutation.start_position)),
        "mutation": mutation.sequence_change or "",
        "annotation": mutation.protein_change or "",
        "gene": mutation.gene or "",
        "description": mutation.product or "",
        "annotated": False,
    }
    row.update(_gene_list_cells(mutation.annotation))
    return row


def describe_mutation(mutation):
    """The descriptive half of a row -- everything about the mutation and nothing about a
    sample -- as breseq's markup when the mutation is annotated, flat text when it is not.

    Shared with `mutint_sample.mutation_matrix`, whose rows are one of these plus a cell per
    sample, so the cross-sample table and the per-sample one render a mutation identically.
    """
    entry = gd_entry(mutation)
    return _annotated_row(entry) if entry else _plain_row(mutation)


def _frequency(call):
    """breseq shows a polymorphism as a percentage; a fixed mutation shows nothing.

    Returns ``(text, is_polymorphism)``.
    """
    frequency = call.frequency
    if frequency is None:
        return "", False
    value = float(frequency)
    if value >= 1:
        return "100%", False
    return "%.1f%%" % (value * 100), True


def build_rows(mutation_calls, browse_url=None, *, ancestral_mutation_ids=frozenset(),
               refseq_url=None):
    """One breseq-style row per mutation call, ready for the template.

    ``browse_url`` is called with a MutationCall and returns where its
    evidence cell should link, or None for no link -- which is how a sample with
    no stored alignment renders its type as plain text.

    ``ancestral_mutation_ids`` tints the rows observed in the experiment's designated
    ancestor. Everything that analyzes the data subtracts them; this table draws them, tinted,
    when the reader asks to see them, because it is what breseq called in one sample and a
    row that is red says why it is missing everywhere else. The caller decides which rows to
    hand over; this only colors what it is given.

    A separate key rather than a third `row_class`: that one is a choice between breseq's
    striping and its green polymorphism fill, and folding a tint into it would cost whichever
    lost. Keyword-only because two callers already pass `browse_url` positionally.

    ``refseq_url`` is the same idea for the Reference column -- called with an
    MutationCall, returning where its contig name should link, or None. It is how a row
    reaches the NCBI Sequence Viewer, and it is None on the NCBI page itself, where the link
    would point at the page you are already on.
    """
    rows = []
    for index, call in enumerate(mutation_calls):
        row = describe_mutation(call.mutation)

        frequency_text, is_polymorphism = _frequency(call)
        row["freq"] = frequency_text
        row["mutation_type"] = call.mutation.mutation_type or ""
        # The plain name, for the row's `data-seq-id`: `seq_id` above is breseq's HTML of it,
        # with hyphens rewritten as non-breaking ones.
        row["seq_id_text"] = call.mutation.seq_id or ""
        row["mutation_id"] = call.mutation_id
        # The call, not the mutation: the editor addresses rows by what it deletes,
        # and one mutation is observed in many samples. Unused by the read-only tables.
        row["call_id"] = call.id
        row["evidence_url"] = browse_url(call) if browse_url else None
        row["refseq_url"] = refseq_url(call) if refseq_url else None
        # breseq alternates row shading and colors a polymorphic call green.
        row["row_class"] = ("polymorphism_table_row" if is_polymorphism
                            else "alternate_table_row_%d" % (index % 2))
        row["ancestral"] = call.mutation_id in ancestral_mutation_ids
        # In column order, for a template, which cannot look a key up by a variable.
        row["gene_list_cells"] = [(key, row[key]) for key, _title, _italic in GENE_LIST_FIELDS]
        rows.append(row)
    return rows

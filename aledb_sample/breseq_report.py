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

from aledb_import.annotate.display import add_html_fields, commify


def is_mixed(reseq):
    """Whether to show breseq's Freq column for this sample.

    Here rather than on either view because both render the table and the answer must be the
    same in both -- a clonal sample has no frequency column on the Samples page and must not
    grow one on the browser page.

    It was `is_population` over a column of that name; both were inverted, and the question
    it answers did not change -- a *mixed* sample is the one with frequencies.
    """
    if reseq is None:
        return False
    return bool(reseq.is_mixed)


def gd_entry(mutation):
    """The annotated GD record for a Mutation, or None if it has no annotation.

    ``gd_data`` alone is not enough: a SNP's Mutation column is "ref→new", and the
    reference base is something annotation derives, not something the .gd carries.
    """
    if not mutation.gd_data or not mutation.annotation:
        return None
    entry = dict(mutation.gd_data)
    entry.update(mutation.annotation)
    return entry


def _annotated_row(entry):
    add_html_fields(entry)
    return {
        "seq_id": entry.get("html_seq_id", ""),
        "position": entry.get("html_position", ""),
        "mutation": entry.get("html_mutation", ""),
        "annotation": entry.get("html_mutation_annotation", ""),
        "gene": entry.get("html_gene_name", ""),
        "description": entry.get("html_gene_product", ""),
        "annotated": True,
    }


def _plain_row(mutation):
    """A row for a mutation with no stored annotation."""
    return {
        "seq_id": mutation.seq_id or "",
        "position": commify(str(mutation.start_position)),
        "mutation": mutation.sequence_change or "",
        "annotation": mutation.protein_change or "",
        "gene": mutation.gene or "",
        "description": mutation.product or "",
        "annotated": False,
    }


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
    ancestor. **This is the one page that shows them**: everything that analyses the data
    subtracts them, but this table is what breseq called in one sample, and a row silently
    missing from it would make the page disagree with the report it was imported from.

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
        entry = gd_entry(call.mutation)
        row = _annotated_row(entry) if entry else _plain_row(call.mutation)

        frequency_text, is_polymorphism = _frequency(call)
        row["freq"] = frequency_text
        row["mutation_type"] = call.mutation.mutation_type or ""
        row["mutation_id"] = call.mutation_id
        # The call, not the mutation: the editor addresses rows by what it deletes,
        # and one mutation is observed in many samples. Unused by the read-only tables.
        row["call_id"] = call.id
        row["evidence_url"] = browse_url(call) if browse_url else None
        row["refseq_url"] = refseq_url(call) if refseq_url else None
        # breseq alternates row shading and colours a polymorphic call green.
        row["row_class"] = ("polymorphism_table_row" if is_polymorphism
                            else "alternate_table_row_%d" % (index % 2))
        row["ancestral"] = call.mutation_id in ancestral_mutation_ids
        rows.append(row)
    return rows

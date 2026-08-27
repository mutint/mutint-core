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


def is_population(reseq):
    """Whether to show breseq's Freq column for this sample.

    Here rather than on either view because both render the table and the answer must be the
    same in both -- a clonal sample has no frequency column on the Samples page and must not
    grow one on the browser page.
    """
    if reseq is None:
        return False
    isolate = getattr(getattr(reseq, "tech_rep", None), "isolate", None)
    return bool(isolate and isolate.is_population)


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
        "seq_id": mutation.reseq_reference or "",
        "position": commify(str(mutation.position)),
        "mutation": mutation.sequence_change or "",
        "annotation": mutation.protein_change or "",
        "gene": mutation.gene or "",
        "description": mutation.product or "",
        "annotated": False,
    }


def _frequency(observed):
    """breseq shows a polymorphism as a percentage; a fixed mutation shows nothing.

    Returns ``(text, is_polymorphism)``.
    """
    frequency = observed.frequency
    if frequency is None:
        return "", False
    value = float(frequency)
    if value >= 1:
        return "100%", False
    return "%.1f%%" % (value * 100), True


def build_rows(observed_mutations, browse_url=None):
    """One breseq-style row per observed mutation, ready for the template.

    ``browse_url`` is called with an ObservedMutation and returns where its
    evidence cell should link, or None for no link -- which is how a sample with
    no stored alignment renders its type as plain text.
    """
    rows = []
    for index, observed in enumerate(observed_mutations):
        entry = gd_entry(observed.mutation)
        row = _annotated_row(entry) if entry else _plain_row(observed.mutation)

        frequency_text, is_polymorphism = _frequency(observed)
        row["freq"] = frequency_text
        row["mutation_type"] = observed.mutation.mutation_type or ""
        row["mutation_id"] = observed.mutation_id
        # The observation, not the mutation: the editor addresses rows by what it deletes,
        # and one mutation is observed in many samples. Unused by the read-only tables.
        row["observed_id"] = observed.id
        row["evidence_url"] = browse_url(observed) if browse_url else None
        # breseq alternates row shading and colours a polymorphic call green.
        row["row_class"] = ("polymorphism_table_row" if is_polymorphism
                            else "alternate_table_row_%d" % (index % 2))
        rows.append(row)
    return rows

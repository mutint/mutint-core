"""Turning validated form fields into the same rows an import would have produced.

A mutation added here has to be indistinguishable from one breseq called, because everything
downstream treats them alike: the mutation tables render from `gd_data` + `annotation`,
`to_gd_line()` round-trips `gd_data` back out for `gdtools APPLY`, and `Mutation.objects
.get_or_create` dedups on seven fields. Get any of that subtly different and a hand-entered
mutation quietly becomes a *second* row the next time the same call is imported.

So this mirrors `gd_import._database_gd_mutations` for a single record, and reuses its rules
rather than restating them -- `synthesize_sequence_change` above all, which is why that
function stopped being private.
"""

import logging

from aledb_import import annotation
from aledb_import.gd_import import synthesize_sequence_change
from aledb_import.gene_annotation import get_annotated_gene_list
from genomediff.records import Record

logger = logging.getLogger(__name__)

#: What `MutationCall.source` records for a mutation someone typed in. The column already
#: distinguishes callers -- imports write "breseq" -- and null means "imported before this
#: column existed", which is a different thing and must not be overloaded.
MANUAL_SOURCE = "manual"


def build_gd_data(mutation_type, attributes):
    """The verbatim GenomeDiff record, as `Mutation.gd_data` stores it.

    Two omissions are deliberate.

    **No `id`.** `Mutation.to_gd_line()` does `data.pop('id', self.id)`, so leaving it out
    makes an exported line carry the Mutation's own primary key -- which exists, is unique, and
    needs nothing invented. A record built before the row exists has no id to give it.

    **No `frequency`.** It is per-call, and `gd_data` lives on the `Mutation`, which
    every sample observing it shares. Putting one sample's frequency there would attach it to
    all of them.
    """
    return dict(attributes, type=mutation_type, parent_ids=None)


def annotate(gd_data, experiment):
    """A copy of the record with breseq's annotation filled in, where that is possible.

    Returns `(record, annotated)`. `annotate_records` answers False rather than raising when
    the experiment has no stored reference, which is a real state -- the mutation is stored
    unannotated and `./aledb reannotate` fills it in once a reference arrives, exactly as for a
    `.gd` imported before one existed.
    """
    record = dict(gd_data)
    annotated = annotation.annotate_records([record], experiment)
    return record, bool(annotated)


def build_identity(mutation_type, gd_data, annotated_record):
    """The ten-key identity `history.apply_changes` mints a `Mutation` from.

    The six `MUTATION_KEY_FIELDS` are the `get_or_create` key, so each has to be derived the
    way `gd_import` derives it. `feature_length` is `size`, which most types do not have;
    `reseq_reference` is the contig. The remaining four ride along as creation defaults.
    """
    attributes = {key: value for key, value in gd_data.items()
                  if key not in ("type", "parent_ids")}

    # Built field by field through `set()` rather than through the constructor's
    # `**attributes`, which stores whatever it is given. `set()` is the checked half of the
    # package's API: it runs breseq's guard for the field and refuses a value gdtools would
    # reject. `validate_record` has already passed by the time we are here, so a ValueError
    # means the form's rules and breseq's have drifted apart -- which is worth failing loudly
    # over rather than storing a mutation that cannot be applied.
    record = Record(mutation_type, None, parent_ids=None)
    for key, value in attributes.items():
        record.set(key, value)

    # `gene` is part of the get_or_create key, so it is derived with gd_import's own
    # expression rather than taken from `display_values` -- the two agree today, and the key
    # is the wrong place to depend on that continuing.
    gene_list = get_annotated_gene_list(
        annotated_record.get("gene_name") or attributes.get("gene_name"),
        annotated_record.get("gene_product") or attributes.get("gene_product"))

    # The four non-key defaults, built by the same functions `apply_to` would use. Carrying
    # them in the identity rather than leaving them to the post-create `apply_to` is what lets
    # a restore from the change log recreate an annotated row rather than a bare one.
    annotated = {}
    annotated.update(annotation.annotation_values(annotated_record))
    annotated.update(annotation.display_values(annotated_record))

    return {
        "position": attributes.get("position"),
        "reseq_reference": attributes.get("seq_id"),
        "mutation_type": mutation_type,
        "feature_length": attributes.get("size"),
        "sequence_change": synthesize_sequence_change(record)[:200],
        "gene": ", ".join(gene_list),
        "gd_data": gd_data,
        "annotation": annotated.get("annotation"),
        "product": annotated.get("product") or "",
        "protein_change": annotated.get("protein_change") or "",
    }


def build_call(frequency):
    """One sample's call of a hand-entered mutation.

    `present` is True because asserting the mutation is the whole point of the form -- an
    MutationCall with `present=False` records that something was looked for and found
    absent, which is not what this page is for. `source` is what records that a person rather
    than a caller said so.
    """
    return {
        "present": True,
        "wt_reads": None,
        "mutated_reads": None,
        "other_reads": None,
        "reference_genome_likelihood": None,
        "frequency": str(frequency),
        "source": MANUAL_SOURCE,
    }


def apply_annotation(mutation, annotated_record):
    """Fill the promoted annotation columns on the row `apply_changes` just minted.

    `history._resolve_mutation` sets only what the identity carries, so `snp_type`,
    `mutation_category`, `gene_name`, `locus_tag`, `start_position` and `end_position` would
    stay null and the mutation would render through the unannotated fallback. This is the same
    call `gd_import` makes after its own `get_or_create`.
    """
    if mutation is None:
        return []
    # No "was it annotated" guard here: the annotator writes *flat* keys onto the record
    # (`gene_name`, `mutation_category`, ...) and it is `annotation_values` that assembles the
    # blob, so testing `record["annotation"]` would always be false and silently apply nothing.
    # `apply_to` already answers [] for a record carrying no annotation.
    return annotation.apply_to(mutation, annotated_record)

"""Annotating imported mutations against an experiment's own reference.

This is the database-aware glue; `aledb_import.annotate` itself knows nothing about
Django. An import path hands over the GD records it just parsed, and gets back
mutations carrying the same gene, codon and amino-acid fields `gdtools ANNOTATE`
would have written.

Annotation reads the experiment's stored reference -- the same canonical GFF3 the
store hashes and igv.js draws. It is written in breseq's own dialect precisely so
one artifact can serve all three; see `aledb_import.reference.normalize_reference`.

Annotation is deliberately **additive**. It fills the columns that were empty
(`protein_change` has always been ""), and never rewrites `sequence_change`,
which `gd_import` uses as part of the mutation dedup key.
"""

import logging
import os

from aledb_import import reference_store
from aledb_import.annotate.annotator import ANNOTATION_KEYS, annotate_mutations
from aledb_import.annotate.display import (
    add_html_fields,
    text_mutation_annotation,
)
from aledb_import.annotate.loader import UnsupportedReferenceFormat, load_reference
from aledb_import.gene_annotation import get_annotated_gene_list

logger = logging.getLogger(__name__)

# Promoted to real columns because they are filtered, counted or sorted on.
# They are also left in the `annotation` blob, so that blob stays self-describing
# and rendering is a plain dict merge rather than a column-by-column rebuild.
PROMOTED_COLUMNS = ('snp_type', 'mutation_category', 'gene_name', 'locus_tag')
POSITION_COLUMNS = (('start_position', 'position_start'),
                    ('end_position', 'position_end'))

_reference_cache = {}


def clear_cache():
    """Drop memoised reference sequences. For tests and for reannotate."""
    _reference_cache.clear()


def reference_sequences_for(experiment):
    """Parsed reference for `experiment`, or None if it has none stored.

    Memoised per process: parsing a bacterial genome takes a couple of seconds and
    every sample in an import annotates against the same reference.
    """
    path = reference_store.annotation_reference_path(experiment.ale_id)
    if not path:
        return None

    key = (experiment.ale_id, os.path.getmtime(path))
    if key not in _reference_cache:
        try:
            _reference_cache[key] = load_reference(path)
        except (UnsupportedReferenceFormat, ValueError) as error:
            logger.warning("cannot annotate experiment %s: %s", experiment.ale_id, error)
            return None
    return _reference_cache[key]


def annotate_records(records, experiment):
    """Annotate parsed GD records in place. Returns True if annotation happened.

    Returns False -- rather than raising -- when the experiment has no usable
    reference. Import paths on this codebase accept bare .gd files that may
    arrive before any reference does, so a missing reference means "not annotated
    yet", not "this import is invalid". `reannotate` fills them in later.
    """
    if not records:
        return False
    references = reference_sequences_for(experiment)
    if references is None:
        return False

    known = set(references.seq_ids())
    annotatable = [r for r in records if r.get('seq_id') in known]
    if not annotatable:
        logger.warning(
            "experiment %s: no mutation matches a reference sequence (%s)",
            experiment.ale_id, ", ".join(sorted(known)[:5]))
        return False

    annotate_mutations(annotatable, references)
    for record in annotatable:
        add_html_fields(record)
    return True


def annotation_values(record):
    """The Mutation field values implied by an annotated GD record.

    Returns {} for a record that was never annotated, so a caller can apply this
    unconditionally without clobbering anything.
    """
    if not record.get('gene_name') and not record.get('mutation_category'):
        return {}

    blob = {key: record[key] for key in ANNOTATION_KEYS
            if record.get(key) not in (None, '')}

    values = {'annotation': blob}
    for column in PROMOTED_COLUMNS:
        values[column] = record.get(column) or ''
    for column, source_key in POSITION_COLUMNS:
        values[column] = _as_int(record.get(source_key))
    return values


def display_values(record):
    """Columns the existing mutation table reads, derived from the annotation.

    `sequence_change` is deliberately absent: `gd_import` uses it as part of the
    dedup key, so changing its shape would fork every mutation on re-import.
    """
    if not record.get('gene_name'):
        return {}

    values = {'protein_change': text_mutation_annotation(record)[:300]}

    gene_list = get_annotated_gene_list(record.get('gene_name'), record.get('gene_product'))
    if gene_list:
        values['gene'] = ', '.join(gene_list)
    product = record.get('gene_product')
    if product:
        values['product'] = product
    return values


def apply_to(mutation, record, save=True):
    """Copy a record's annotation onto a Mutation. Returns the fields it changed."""
    values = {}
    values.update(annotation_values(record))
    values.update(display_values(record))
    if not values:
        return []

    for field, value in values.items():
        setattr(mutation, field, value)
    if save:
        mutation.save(update_fields=sorted(values))
    return sorted(values)


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

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

from django.db import transaction

from aledb_import import reference_store
from aledb_import.annotate.annotator import ANNOTATION_KEYS, annotate_mutations
from aledb_import.annotate.display import (
    add_html_fields,
    text_mutation_annotation,
)
from aledb_import.annotate.loader import UnsupportedReferenceFormat, load_reference
from aledb_import.gene_annotation import get_annotated_gene_list

logger = logging.getLogger(__name__)


class ReferenceUnavailable(Exception):
    """The experiment has no reference that can be read back and annotated against."""

# Promoted to real columns because they are filtered, counted or sorted on.
# They are also left in the `annotation` blob, so that blob stays self-describing
# and rendering is a plain dict merge rather than a column-by-column rebuild.
PROMOTED_COLUMNS = ('snp_type', 'mutation_category', 'gene_name', 'locus_tag')
# `start_position` is deliberately absent: it is set at creation from the record's own
# `position` and is part of `MUTATION_KEY_FIELDS`, so re-annotation must not rewrite it.
# `mutation_interval` computes the identical value, which is what makes leaving it out safe.
POSITION_COLUMNS = (('end_position', 'position_end'),)

_reference_cache = {}


def clear_cache():
    """Drop memoised reference sequences. For tests and for reannotate."""
    _reference_cache.clear()


def reference_sequences_for(experiment):
    """Parsed reference for `experiment`, or None if it has none stored.

    Memoised per process: parsing a bacterial genome takes a couple of seconds and
    every sample in an import annotates against the same reference.
    """
    path = reference_store.annotation_reference_path(experiment.id)
    if not path:
        return None

    key = (experiment.id, os.path.getmtime(path))
    if key not in _reference_cache:
        try:
            _reference_cache[key] = load_reference(path)
        except (UnsupportedReferenceFormat, ValueError) as error:
            logger.warning("cannot annotate experiment %s: %s", experiment.id, error)
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
    return annotate_records_with(records, references, experiment.id)


def annotate_records_with(records, references, experiment_id=None):
    """Annotate records against an already-loaded reference. Returns True if it ran.

    Records naming a contig the reference does not have are left alone rather
    than annotated against nothing.
    """
    known = set(references.seq_ids())
    annotatable = [r for r in records if r.get('seq_id') in known]
    if not annotatable:
        logger.warning(
            "experiment %s: no mutation matches a reference sequence (%s)",
            experiment_id, ", ".join(sorted(known)[:5]))
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


def sample_groups(experiment, mutations):
    """The experiment's mutations, grouped by the sample they were observed in.

    Annotation is per sample because breseq's same-codon SNP merge is: two
    consensus SNPs sharing a codon only inform each other if they were seen
    together. A mutation observed in several samples is annotated once, with the
    first group that contains it, so the result does not depend on iteration
    order.
    """
    from aledb_sample.models import MutationCall

    by_id = {mutation.pk: mutation for mutation in mutations}
    calls = (MutationCall.objects
                .filter(mutation__experiment=experiment)
                .values_list("sample_id", "mutation_id")
                .order_by("sample_id", "mutation_id"))

    groups, assigned = {}, set()
    for sample_id, mutation_id in calls.iterator():
        if mutation_id in assigned or mutation_id not in by_id:
            continue
        assigned.add(mutation_id)
        groups.setdefault(sample_id, []).append(by_id[mutation_id])

    orphans = [m for pk, m in by_id.items() if pk not in assigned]
    if orphans:
        groups[None] = orphans
    return list(groups.values())


def differs(mutation, record):
    """Whether re-annotating would change anything stored for this mutation."""
    values = {}
    values.update(annotation_values(record))
    values.update(display_values(record))
    return any(getattr(mutation, field) != value for field, value in values.items())


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


def reannotate_experiment(experiment, mutations=None, references=None, dry_run=False,
                          on_error=None):
    """Re-derive annotation for an experiment against its stored reference.

    Returns `(annotated, changed, skipped, failed)`.

    Extracted from `./aledb reannotate` so the command and the contig-rename path share one
    rule rather than growing two that can disagree. A rename in particular *must* re-annotate:
    `annotate_records_with` filters records on `record['seq_id']`, so a mutation still naming
    the old contig matches nothing and is silently left unannotated.

    `on_error` receives a message per sample that fails; one bad sample never stops the rest.
    """
    from aledb_sample.models import Mutation

    if mutations is None:
        mutations = list(Mutation.objects.filter(experiment=experiment))
    if references is None:
        references = reference_sequences_for(experiment)
    if references is None:
        raise ReferenceUnavailable(
            "Experiment %s has no readable reference to annotate against."
            % (experiment.id,))

    annotated = changed = failed = 0
    skipped = sum(1 for mutation in mutations if not mutation.genome_diff)

    for group in sample_groups(experiment, mutations):
        payloads = [(mutation, dict(mutation.genome_diff))
                    for mutation in group if mutation.genome_diff]
        if not payloads:
            continue

        records = [record for _mutation, record in payloads]
        try:
            annotate_records_with(records, references)
        except Exception as error:  # noqa: BLE001 - one bad sample must not stop the rest
            failed += len(payloads)
            if on_error is not None:
                on_error("  failed to annotate a sample: %s" % error)
            else:
                logger.warning("failed to annotate a sample of experiment %s: %s",
                               experiment.id, error)
            continue

        for mutation, record in payloads:
            annotated += 1
            if not differs(mutation, record):
                continue
            changed += 1
            if not dry_run:
                with transaction.atomic():
                    apply_to(mutation, record)

    return annotated, changed, skipped, failed

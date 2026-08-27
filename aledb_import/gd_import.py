"""Import breseq GenomeDiff (.gd) mutation entries directly from uploaded files.

This is the one place mutations are stored. It used to be the HTML-free counterpart
to a second, breseq-directory-shaped importer; that one is gone and ``./aledb upload``
comes through here too:
the CLI upload path reads a full breseq output directory (``.gd`` + ``index.html``
+ ``summary.html``), while this path takes bare ``.gd`` files dropped in the web
UI and reads everything it needs from the GenomeDiff itself.

Parsing uses the ``genomediff`` package. Each mutation's full parsed record is
stored verbatim in ``Mutation.gd_data`` so it can be round-tripped back to a
``.gd`` line for ``gdtools APPLY`` (see ``Mutation.to_gd_line``).

Imported mutations are attached to the normal experiment hierarchy
(``AleExperiment -> AleId -> Flask -> Isolate -> TechnicalReplicate ->
ResequencingExperiment -> ObservedMutation``) so they appear in the existing
mutation tables, stats, and dashboards with no extra plumbing. The chain is
synthesized the same way the CLI does it: default Instrument/Media/FreezerBox
placeholders, and the A-F-I-R identity parsed from each filename.
"""

import logging
import os
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max

import aledb_metadata.parser as metadata_defaults
from aledb_experiment.models import (
    AleExperiment,
    AleId,
    Flask,
    FreezerBox,
    Instrument,
    Isolate,
    Media,
    Project,
    TechnicalReplicate,
)
from aledb_import import annotation
from aledb_import import sniff
from aledb_import.gene_annotation import get_annotated_gene_list
from aledb_import.util import AleName, parse_ale_name
from aledb_seq.models import (
    Mutation,
    ObservedMutation,
    ResequencingExperiment,
    UnassignedMissingCoverageEvidence,
)

from genomediff import GenomeDiff
from genomediff.records import TYPE_SPECIFIC_FIELDS

logger = logging.getLogger("aledb_import.gd_import")

# Which caller produced an observation. Recorded per row so other variant
# callers can be added alongside breseq later.
BRESEQ_SOURCE = "breseq"


class SeqIdMismatch(Exception):
    """A .gd names a contig the experiment's reference does not have."""


class ReferenceRequired(Exception):
    """The target experiment has no reference genome, and a .gd cannot supply one."""


class NotAGenomeDiff(Exception):
    """A file imported as a .gd declares itself to be some other format."""


def import_gd_files(uploaded_files, project_name, experiment_name, person, is_public=False,
                    require_reference=True):
    """Import a batch of dropped ``.gd`` files into a single AleExperiment.

    ``uploaded_files`` is an iterable of file-like objects each exposing ``.name``
    and ``.read()`` (e.g. Django ``UploadedFile``). Each file's A-F-I-R identity is
    parsed from its filename. Returns a JSON-serializable summary dict.

    A ``.gd`` carries no reference genome, so it cannot establish the reference every
    sample in an experiment shares -- the experiment must already have one, set by dropping a
    GenBank, GFF3 or FASTA on the Add page. ``require_reference=False`` is for callers that
    manage that invariant themselves.
    """
    from aledb_import.reference_store import has_reference

    context = _prepare_experiment(project_name, experiment_name, person, is_public)
    if require_reference and not has_reference(context["experiment"]):
        raise ReferenceRequired(
            "Experiment %r has no reference genome. A .gd carries none, so add one "
            "(GenBank, GFF3 or FASTA) first, or import a breseq result folder instead."
            % (context["experiment"].name,))

    file_results = []
    total_mutations = 0
    for uploaded in uploaded_files:
        filename = os.path.basename(getattr(uploaded, "name", "") or "unnamed.gd")
        try:
            with transaction.atomic():
                count = _import_one_file(uploaded, filename, context, person)
            file_results.append({"file": filename, "mutations": count, "error": None})
            total_mutations += count
        except Exception as exc:  # one bad file must not poison the batch
            logger.exception("GenomeDiff import failed for %s", filename)
            file_results.append({"file": filename, "mutations": 0, "error": str(exc)})

    experiment = context["experiment"]
    if total_mutations:
        run_post_processing(experiment)

    return {
        "experiment_id": experiment.ale_id,
        "experiment": experiment.name,
        "total_mutations": total_mutations,
        "files": file_results,
    }


def _prepare_experiment(project_name, experiment_name, person, is_public):
    """Get/create the target project, experiment, and the shared placeholder
    Media / FreezerBox / Instrument, mirroring ``ale_experiment.create_ale_experiment``."""
    from aledb_import.ale_experiment import try_creating_project

    try:
        project = Project.objects.get(name=project_name)
    except Project.DoesNotExist:
        project = try_creating_project(project_name, person, is_public)

    instrument, _ = Instrument.objects.get_or_create(
        name=metadata_defaults.DEFAULT_INSTRUMENT_NAME)
    experiment, _ = AleExperiment.objects.get_or_create(
        name=experiment_name, instrument=instrument, person=person, project=project)
    media, _ = Media.objects.get_or_create(
        description=metadata_defaults.DEFAULT_MEDIA_DESCRIPTION,
        substrate=metadata_defaults.DEFAULT_MEDIA_SUBSTRATE,
        temperature=metadata_defaults.DEFAULT_TEMPERATURE,
        volume=metadata_defaults.DEFAULT_VOLUME,
        stirring_speed=metadata_defaults.DEFAULT_STIRRING_SPEED)
    freezer_box, _ = FreezerBox.objects.get_or_create(
        name=metadata_defaults.DEFAULT_FREEZER_BOX_NAME,
        number=metadata_defaults.DEFAULT_FREEZER_BOX_NUMBER)

    return {"experiment": experiment, "media": media, "freezer_box": freezer_box}


def prepare_experiment_by_id(ale_experiment_id):
    """Context for adding to an *existing* experiment, identified by primary key.

    The web paths use this rather than `_prepare_experiment`, whose name-based
    get_or_create keys on (name, instrument, person, project) -- so the same experiment name
    with a different person silently forks into a second experiment. Keying on the pk means
    two people can add to one experiment, and two experiments may share a name.

    It also avoids `try_creating_project` -> `find_user`, which prompts on stdin and therefore
    cannot run inside a web request.
    """
    experiment = AleExperiment.objects.get(pk=ale_experiment_id)
    media, _ = Media.objects.get_or_create(
        description=metadata_defaults.DEFAULT_MEDIA_DESCRIPTION,
        substrate=metadata_defaults.DEFAULT_MEDIA_SUBSTRATE,
        temperature=metadata_defaults.DEFAULT_TEMPERATURE,
        volume=metadata_defaults.DEFAULT_VOLUME,
        stirring_speed=metadata_defaults.DEFAULT_STIRRING_SPEED)
    freezer_box, _ = FreezerBox.objects.get_or_create(
        name=metadata_defaults.DEFAULT_FREEZER_BOX_NAME,
        number=metadata_defaults.DEFAULT_FREEZER_BOX_NUMBER)
    return {"experiment": experiment, "media": media, "freezer_box": freezer_box}


def _import_one_file(uploaded, filename, context, person):
    document = _parse_document(uploaded)
    sample_name = filename[:-3] if filename.lower().endswith(".gd") else filename
    _, count = import_document_as_sample(document, sample_name, context, person)
    return count


def import_document_as_sample(document, sample_name, context, person):
    """Build the experiment chain for ``sample_name`` and write the document's mutations.

    Shared by the bare-.gd upload, where the sample name comes from the filename, and the
    breseq folder import, where it comes from the sample directory -- so both derive sample
    identity through exactly one rule. Returns ``(seq_experiment, mutation_count)``.
    """
    _check_seq_ids(document, context["experiment"], sample_name)

    afir = _parse_afir(sample_name)

    if afir is None:
        # Name carries no A-F-I-R identity (e.g. "Ara-1_500gen_762B"). Give the sample
        # its own isolate rather than letting every such name collapse onto 1-1-1-1.
        seq_experiment = _get_or_create_autonumbered_chain(
            context, document, person, sample_name)
    else:
        ale_number, flask_number, isolate_number, tech_rep_number = afir
        seq_experiment = _get_or_create_chain(
            context, document, ale_number, flask_number, isolate_number,
            tech_rep_number, person, sample_name)

    return seq_experiment, _database_gd_mutations(
        seq_experiment, document, context.get("experiment"))


def _parse_afir(sample_name):
    """Return ``(ale, flask, isolate, tech_rep)`` if the name is A-F-I-R, else ``None``.

    ``util.parse_ale_name`` silently returns 1 for any field it cannot read, which is the
    behaviour the CLI path relies on but which here would map every non-conforming filename
    onto the same sample. This is the strict counterpart: all four fields must be present
    and integral, or the caller falls back to auto-numbering."""
    split = sample_name.split("-")
    if len(split) <= AleName.TechnicalReplicate:
        return None
    try:
        return tuple(
            int(split[i]) for i in (
                AleName.Ale, AleName.Flask, AleName.Isolate, AleName.TechnicalReplicate))
    except ValueError:
        return None


def _parse_document(uploaded):
    """Parse an uploaded ``.gd`` into a ``genomediff.GenomeDiff``.

    Decodes bytes to text and drops blank lines (the genomediff parser raises on
    a line it can't match, and a bare newline matches nothing).

    Only mutations are read from the result. GenomeDiff spells a mutation with a
    three-letter code and the parser classifies on that, so evidence (RA, MC, JC,
    CN, UN, SC, PD) and validation entries never reach ``document.mutations``.
    Nothing is filtered out of the text beforehand: reading an entry type the
    parser does not recognise is genomediff's job, and it does not fail a file over
    one -- see the unknown-type handling in its parser.
    """
    raw = uploaded.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    # The mirror of the GenomeDiff check in `reference.detect_format`: a reference genome
    # named `.gd` otherwise reaches the GenomeDiff parser, which reports the first line it
    # cannot match rather than the fact that this is a GenBank and belongs on another type.
    # Only a positively identified other format is refused -- anything the first line does
    # not name is still the parser's to judge.
    kind = sniff.kind_of_text(raw)
    if kind is not None and kind != sniff.KIND_GENOMEDIFF:
        raise NotAGenomeDiff(
            "this is %s, not a GenomeDiff -- import it with the 'Reference genome' type"
            % (sniff.describe(kind),))
    lines = [line for line in raw.splitlines() if line.strip()]
    return GenomeDiff.read(iter(lines))


def _get_or_create_chain(context, document, ale_number, flask_number,
                         isolate_number, tech_rep_number, person, sample_name):
    """Synthesize the experiment chain down to a ResequencingExperiment, reading
    reference/date/type hints from the GenomeDiff header (no breseq HTML)."""
    experiment = context["experiment"]
    metadata = document.metadata

    reseq_reference = metadata.get("REFSEQ", "") or ""
    reseq_date = metadata.get("CREATED", "") or ""
    # breseq marks population (polymorphism) runs with -p in the command line.
    is_population = " -p" in (metadata.get("COMMAND", "") or "")

    ale_id, _ = AleId.objects.get_or_create(ale_experiment=experiment, ale_id=ale_number)
    flask, _ = Flask.objects.get_or_create(
        flask_number=flask_number, ale_id=ale_id, media=context["media"])
    isolate, _ = Isolate.objects.get_or_create(
        flask=flask,
        isolate_number=isolate_number,
        is_population=is_population,
        reseq_reference=reseq_reference[:200],
        reseq_date=reseq_date[:200],
        freezer_box=context["freezer_box"],
        person=person)
    tech_rep, _ = TechnicalReplicate.objects.get_or_create(
        tech_rep_number=tech_rep_number, isolate=isolate)
    seq_experiment, _ = ResequencingExperiment.objects.get_or_create(
        tech_rep=tech_rep, sample_name=sample_name, person=person)
    return seq_experiment


def _get_or_create_autonumbered_chain(context, document, person, sample_name):
    """Chain for a sample whose filename has no A-F-I-R identity.

    Everything hangs off ALE 1 / Flask 1, but each distinct sample gets its own isolate so
    the samples stay individually addressable. Re-importing a sample must not allocate a
    second isolate, so an existing chain for this sample name is reused."""
    experiment = context["experiment"]

    existing = ResequencingExperiment.objects.filter(
        sample_name=sample_name,
        tech_rep__isolate__flask__ale_id__ale_experiment=experiment).first()
    if existing is not None:
        return existing

    metadata = document.metadata
    ale_id, _ = AleId.objects.get_or_create(ale_experiment=experiment, ale_id=1)
    flask, _ = Flask.objects.get_or_create(
        flask_number=1, ale_id=ale_id, media=context["media"])

    next_isolate_number = (Isolate.objects.filter(flask=flask).aggregate(
        Max("isolate_number"))["isolate_number__max"] or 0) + 1

    isolate = Isolate.objects.create(
        flask=flask,
        isolate_number=next_isolate_number,
        # ale_flask_isolate_str() prefers the description, so this is what makes the
        # sample show up as "Ara-1_500gen_762B" rather than a generic "A1 F1 I3 R1".
        description=sample_name[:300],
        is_population=" -p" in (metadata.get("COMMAND", "") or ""),
        reseq_reference=(metadata.get("REFSEQ", "") or "")[:200],
        reseq_date=(metadata.get("CREATED", "") or "")[:200],
        freezer_box=context["freezer_box"],
        person=person)
    tech_rep = TechnicalReplicate.objects.create(tech_rep_number=1, isolate=isolate)
    return ResequencingExperiment.objects.create(
        tech_rep=tech_rep, sample_name=sample_name, person=person)


def _check_seq_ids(document, experiment, sample_name):
    """Refuse a .gd whose contigs are not the experiment's reference.

    Nothing checked this before, which is how an experiment ended up holding
    mutations on `REL606` while its own reference was stored as `REL606.6`. The
    annotator then quietly skipped every record whose seq_id it could not resolve,
    so the failure showed up as missing annotation rather than as a rejected
    import.

    Matched exactly, with no version-suffix trimming -- see
    ``reference_store.known_seq_ids`` for why ALEdb does not follow breseq here.
    An experiment with no reference yet is not checked: a .gd cannot establish one,
    and ``import_gd_files`` already refuses that case earlier with a better message.
    """
    from aledb_import.reference_store import known_seq_ids

    known = known_seq_ids(experiment)
    if not known:
        return

    found = {
        str(record.attributes.get("seq_id"))
        for record in document.mutations
        if record.attributes.get("seq_id")
    }
    unknown = sorted(found - known)
    if unknown:
        raise SeqIdMismatch(
            "%s names %s, which is not this experiment's reference (%s). The same "
            "genome under a different name has to be renamed to match -- upload that "
            "reference on the Add data page, or run `./aledb rename_contigs %s --ref "
            "<file>` -- and a different genome belongs in its own experiment."
            % (sample_name, ", ".join(unknown), ", ".join(sorted(known)),
               experiment.ale_id if experiment is not None else "<id>"))


def _database_gd_mutations(seq_experiment, document, experiment=None):
    """Create Mutation + ObservedMutation rows from the parsed mutations.

    Re-importing the same sample is idempotent: existing ObservedMutations for this
    ResequencingExperiment are cleared first, and Mutations are deduplicated via
    get_or_create (per experiment).

    When the experiment has a reference, the records are annotated first, so gene,
    codon and amino-acid fields come from the reference rather than from whatever
    the .gd happened to carry. A .gd dropped before any reference simply imports
    unannotated; `./aledb reannotate` fills it in once one arrives."""
    ObservedMutation.objects.filter(sequencing_experiment=seq_experiment).delete()

    records = list(document.mutations)
    verbatim = [{
        "type": record.type,
        "id": record.id,
        "parent_ids": record.parent_ids,
        **dict(record.attributes),
    } for record in records]

    # Annotate copies: gd_data is contractually verbatim, and to_gd_line() splats
    # every key of it onto the line it emits for gdtools APPLY.
    annotated = [dict(entry) for entry in verbatim]
    if experiment is not None:
        annotation.annotate_records(annotated, experiment)

    observed_mutations = []
    for record, gd_data, annotated_record in zip(records, verbatim, annotated):
        attributes = dict(record.attributes)
        gene_list = get_annotated_gene_list(
            annotated_record.get("gene_name") or attributes.get("gene_name"),
            annotated_record.get("gene_product") or attributes.get("gene_product"))
        gene_str = ", ".join(gene_list)
        sequence_change = synthesize_sequence_change(record)[:200]

        mutation, created = Mutation.objects.get_or_create(
            ale_experiment=experiment,
            position=attributes.get("position"),
            reseq_reference=attributes.get("seq_id"),
            mutation_type=record.type,
            feature_length=attributes.get("size"),
            sequence_change=sequence_change,
            gene=gene_str,
            defaults={
                "gd_data": gd_data,
                "product": attributes.get("gene_product") or "",
                "protein_change": "",
            })
        if not created and not mutation.gd_data:
            # Backfill a pre-existing (e.g. CLI-imported) row so it becomes APPLY-complete.
            mutation.gd_data = gd_data
            mutation.save(update_fields=["gd_data"])
        annotation.apply_to(mutation, annotated_record)

        observed_mutations.append(ObservedMutation(
            sequencing_experiment=seq_experiment,
            mutation=mutation,
            present=True,
            breseq_present=True,
            source=BRESEQ_SOURCE,
            frequency=_coerce_frequency(attributes.get("frequency"))))

    ObservedMutation.objects.bulk_create(observed_mutations)
    _database_missing_coverage(seq_experiment, document)
    return len(observed_mutations)


def _database_missing_coverage(seq_experiment, document):
    """Record MC (missing coverage) evidence from the .gd.

    aledb_stats reads these to report uncovered regions per sample. Only the
    breseq-directory CLI path used to write them, so a web-imported sample had
    none; both paths go through here now.
    """
    UnassignedMissingCoverageEvidence.objects.filter(
        sequencing_experiment=seq_experiment).delete()
    for record in document.evidence:
        if record.type != "MC":
            continue
        attributes = record.attributes
        UnassignedMissingCoverageEvidence.objects.get_or_create(
            seq_id=attributes.get("seq_id"),
            start=attributes.get("start"),
            end=attributes.get("end"),
            sequencing_experiment=seq_experiment)


def export_gd_text(seq_experiment):
    """Reconstruct a GenomeDiff (.gd) file for a ResequencingExperiment's mutations.

    The result is a valid ``.gd`` accepted by ``gdtools APPLY`` (resolution of MOB
    ``repeat_name`` / CON/INT ``region`` still requires the reference genbank named
    in ``#=REFSEQ``)."""
    lines = ["#=GENOME_DIFF\t1.0"]
    isolate = seq_experiment.tech_rep.isolate if seq_experiment.tech_rep else None
    reseq_reference = getattr(isolate, "reseq_reference", "") or ""
    if reseq_reference:
        lines.append("#=REFSEQ\t%s" % reseq_reference)

    observed = (ObservedMutation.objects
                .filter(sequencing_experiment=seq_experiment)
                .select_related("mutation")
                .order_by("mutation__position"))
    for observed_mutation in observed:
        gd_line = observed_mutation.mutation.to_gd_line()
        if gd_line:
            lines.append(gd_line)
    return "\n".join(lines) + "\n"


def synthesize_sequence_change(record):
    """Build a short human-readable allele description used for display and as the
    dedup discriminator (the discrete alleles themselves live in ``gd_data``).

    Public because `aledb_mutation_editor` builds mutations by hand and has to land on the
    same string this does. `sequence_change` is one of the seven fields
    `Mutation.objects.get_or_create` keys on below, so a second rule for it would let a
    hand-entered mutation and a later re-import of the same call become two rows.
    """
    attributes = record.attributes
    mutation_type = record.type
    if mutation_type in ("SNP", "INS", "SUB"):
        return str(attributes.get("new_seq", ""))
    if mutation_type == "DEL":
        return "del %s bp" % attributes.get("size", "?")
    if mutation_type == "MOB":
        sign = "+" if attributes.get("strand") == 1 else "-"
        return "%s (%s) +%s bp" % (
            attributes.get("repeat_name", ""), sign, attributes.get("duplication_size", 0))
    if mutation_type == "AMP":
        return "%s bp x%s" % (attributes.get("size", "?"), attributes.get("new_copy_number", "?"))
    if mutation_type == "INV":
        return "inv %s bp" % attributes.get("size", "?")
    if mutation_type in ("CON", "INT"):
        return str(attributes.get("region", ""))
    return " ".join(
        str(attributes.get(field))
        for field in TYPE_SPECIFIC_FIELDS.get(mutation_type, ())
        if attributes.get(field) is not None)


def _coerce_frequency(value):
    """ObservedMutation.frequency is Decimal(5,4); default clonal 1.0."""
    if value is None:
        return Decimal("1.0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("1.0")


def run_post_processing(experiment):
    """Recompute derived data so imported mutations surface everywhere the CLI
    path's do (filters, plugin rebuilds, stats, dashboard).

    Public because re-annotation needs it too: changing a mutation's annotation
    changes what convergence, fixation and the dashboard counts see.

    This was four statements naming four things by hand -- the filter row, the plugin hooks,
    the needle-plot data and the dashboard -- and adding a fifth meant editing this function,
    which is what kept core's derived data reachable only from the import path. All five are
    registered rebuilds now, so the list lives in `aledb_common.rebuild_registry` and this
    asks for all of them.

    Eager, and marked stale first. Eager because an import is already a long operation and
    its whole point is to leave the data queryable, so the Overview should be warm when it
    finishes rather than making its first reader wait. Marked stale first so that a rebuild
    which fails is recorded as still needing to run -- the old code left no trace of that at
    all, and a plugin hook that raised took the whole import down with it."""
    from aledb_common.rebuild_registry import request_rebuild, run_rebuilds

    request_rebuild(experiment.ale_id, reason='experiment data imported')
    run_rebuilds(experiment.ale_id)

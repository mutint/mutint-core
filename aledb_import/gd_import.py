"""Import breseq GenomeDiff (.gd) mutation entries directly from uploaded files.

This is the one place mutations are stored. It used to be the HTML-free counterpart
to a second, breseq-directory-shaped importer; that one is gone and ``./aledb import``
comes through here too:
the CLI upload path reads a full breseq output directory (``.gd`` + ``index.html``
+ ``summary.html``), while this path takes bare ``.gd`` files dropped in the web
UI and reads everything it needs from the GenomeDiff itself.

Parsing uses the ``genomediff`` package. Each mutation's full parsed record is
stored verbatim in ``Mutation.supplemental_data["aledb_core"]["genome_diff"]`` so it can be
round-tripped back to a ``.gd`` line for ``gdtools APPLY`` (see ``Mutation.to_gd_line``).

Imported mutations are attached to the normal experiment hierarchy
(``Experiment -> Population -> Sample -> MutationCall``) so they appear in the existing
mutation tables, stats and dashboards with no extra plumbing. The chain is synthesized from
the A-F-I-R identity parsed from each filename, and nothing else -- there used to be
placeholder `Media`, `Instrument` and `FreezerBox` rows to satisfy required foreign keys
that nothing ever read.
"""

import logging
import os

from django.db import transaction

from aledb_experiment.models import (
    Experiment,
    Population,
    Project,
)
from aledb_import import annotation
from aledb_import import sniff
from aledb_import.gene_annotation import get_annotated_gene_list
from aledb_import import sample_names
from aledb_import.sample_names import parse_sample_identity
from aledb_sample.models import (
    Mutation,
    MutationCall,
    Sample,
    UncalledRegion,
)

from genomediff import GenomeDiff
from genomediff.records import TYPE_SPECIFIC_FIELDS
from aledb_experiment import paths

logger = logging.getLogger("aledb_import.gd_import")

# Which caller produced a call. Recorded per row so other variant
# callers can be added alongside breseq later.
BRESEQ_SOURCE = "breseq"


class SeqIdMismatch(Exception):
    """A .gd names a contig the experiment's reference does not have."""


class ReferenceRequired(Exception):
    """The target experiment has no reference genome, and a .gd cannot supply one."""


class NotAGenomeDiff(Exception):
    """A file imported as a .gd declares itself to be some other format."""


def import_gd_files(uploaded_files, project_name, experiment_name, owner_name,
                    is_public=False,
                    require_reference=True):
    """Import a batch of dropped ``.gd`` files into a single Experiment.

    ``uploaded_files`` is an iterable of file-like objects each exposing ``.name``
    and ``.read()`` (e.g. Django ``UploadedFile``). Each file's A-F-I-R identity is
    parsed from its filename. Returns a JSON-serializable summary dict.

    A ``.gd`` carries no reference genome, so it cannot establish the reference every
    sample in an experiment shares -- the experiment must already have one, set by dropping a
    GenBank, GFF3 or FASTA on the Add page. ``require_reference=False`` is for callers that
    manage that invariant themselves.
    """
    from aledb_import.reference_store import has_reference

    context = _prepare_experiment(project_name, experiment_name, owner_name, is_public)
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
                count, warnings = _import_one_file(uploaded, filename, context)
            file_results.append({"file": filename, "mutations": count, "error": None,
                                 "warnings": warnings})
            total_mutations += count
        except Exception as exc:  # one bad file must not poison the batch
            logger.exception("GenomeDiff import failed for %s", filename)
            file_results.append({"file": filename, "mutations": 0, "error": str(exc),
                                 "warnings": []})

    experiment = context["experiment"]
    if total_mutations:
        run_post_processing(experiment)

    return {
        "experiment_id": experiment.id,
        "experiment": experiment.name,
        "total_mutations": total_mutations,
        "files": file_results,
    }


def _prepare_experiment(project_name, experiment_name, owner_name, is_public):
    """Get/create the target project and experiment.

    There used to be placeholder `Media`, `Instrument` and `FreezerBox` rows here as well.
    All three were required foreign keys to singleton rows nothing ever read or displayed --
    the instrument's name was the empty string -- so all three are gone, `Media` last, with
    the table.
    """
    from aledb_import.ale_experiment import try_creating_project

    try:
        project = Project.objects.get(name=project_name)
    except Project.DoesNotExist:
        project = try_creating_project(project_name, owner_name, is_public)

    experiment, _ = Experiment.objects.get_or_create(
        name=experiment_name, project=project)
    return {"experiment": experiment}


def prepare_experiment_by_id(experiment_id):
    """Context for adding to an *existing* experiment, identified by primary key.

    The web paths use this rather than `_prepare_experiment`, whose name-based
    get_or_create can only ever reach one experiment of a given name in a given project --
    and two experiments may legitimately share a name. Keying on the pk is what reaches the
    second one.

    It also avoids `try_creating_project` -> `find_user`, which prompts on stdin and therefore
    cannot run inside a web request.
    """
    experiment = Experiment.objects.get(pk=experiment_id)
    return {"experiment": experiment}


def _is_storable(record):
    """Whether a parsed mutation has enough to become a `Mutation` row.

    The parser is lenient now: a line missing positional columns is read as far as it goes,
    with the missing fields set to None, and the problem recorded in `document.parse_errors`.
    That is the right call for reading a file -- one bad line should not cost the other
    hundred -- but such a record cannot be stored: `Mutation.start_position` is NOT NULL, so
    handing it on turns a reported bad line into an IntegrityError that rolls back the whole
    sample. Skipping it here is what makes leniency actually lenient.

    Nothing is lost by the skip: the reason is already in `parse_errors`, which the import
    summary reports per file, so the person who uploaded it is told which line and why.
    """
    return record.get("position", None) is not None and record.get("seq_id", None) is not None


def parse_warnings(document):
    """What the parser could not make sense of, as sentences for the person who uploaded it.

    Non-fatal by construction: the mutations it *could* read are already imported by the time
    anyone sees these. Surfacing them is what stops a truncated line being silently worth
    fewer mutations than the file appears to contain.
    """
    return [str(error) for error in getattr(document, "parse_errors", ())]


def _import_one_file(uploaded, filename, context):
    document = _parse_document(uploaded)
    sample_name = filename[:-3] if filename.lower().endswith(".gd") else filename
    _, count, _replaced = import_document_as_sample(
        document, sample_name, context)
    return count, parse_warnings(document)


def import_document_as_sample(document, sample_name, context):
    """Build the experiment chain for ``sample_name`` and write the document's mutations.

    Shared by the bare-.gd upload, where the sample name comes from the filename, and the
    breseq folder import, where it comes from the sample directory -- so both derive sample
    identity through exactly one rule.

    Returns ``(seq_experiment, mutation_count, replaced)``, where ``replaced`` is how many
    calls this sample already had. **Re-importing a sample is deliberately allowed
    and deliberately destructive**: `_database_gd_mutations` clears the sample's
    calls before writing its own, which is how a corrected breseq run replaces the
    call set it supersedes, and `test_reimport_is_idempotent` pins it.

    What that leaves is a silence worth breaking. A drop containing a sample the experiment
    already holds does not add to it, it *supersedes* it -- so the count is handed back for
    the caller to report. This is not the same as two folders of one name inside a single
    drop, which `breseq_folder._import_samples` refuses outright: there, neither is an
    update of the other and there is no way to tell which was meant.
    """
    _check_seq_ids(document, context["experiment"], sample_name)

    identity = parse_sample_identity(sample_name)

    if identity is None:
        # The name says nothing about where the sample belongs. Give it its own isolate
        # rather than letting every such name collapse onto 1-1-1-1.
        seq_experiment = _get_or_create_autonumbered_chain(
            context, document, sample_name)
    else:
        seq_experiment = _get_or_create_chain(
            context, document, identity.ale, identity.flask, identity.isolate,
            identity.replicate, sample_name,
            # A label only where the name carries one. `3-30000-1-1` says exactly what the
            # coordinate says, and `label` prefers the description over the
            # computed `A3 F30000 I1-1` -- so filling it for an A-F-I-R sample would
            # relabel every table column with the filename it came from.
            isolate_description=(sample_name
                                 if identity.shape == sample_names.SHAPE_TRIPLE else ""))

    # Counted before the write, which is what clears them.
    replaced = MutationCall.objects.filter(
        sample=seq_experiment).count()

    return seq_experiment, _database_gd_mutations(
        seq_experiment, document, context.get("experiment")), replaced


def _parse_document(uploaded):
    """Parse an uploaded ``.gd`` into a ``genomediff.GenomeDiff``.

    Decodes bytes to text and drops blank lines (a bare newline matches no entry
    pattern, and the parser would report every one of them).

    Read leniently, which is the parser's default: a line it cannot fully make sense of
    lands in ``document.parse_errors`` and the rest of the file still loads. `strict=True`
    would restore the old raise-and-lose-the-file behavior, and is deliberately not used --
    it raises on *any* problem including the field-guard violations breseq's own output
    contains, so it would start refusing files that import cleanly today. The errors are
    reported per file instead; see `parse_warnings`.

    Only mutations are read from the result. GenomeDiff spells a mutation with a
    three-letter code and the parser classifies on that, so evidence (RA, MC, JC,
    CN, UN, SC, PD) and validation entries never reach ``document.mutations``.
    Nothing is filtered out of the text beforehand: reading an entry type the
    parser does not recognize is genomediff's job, and it does not fail a file over
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
                         isolate_number, tech_rep_number, sample_name,
                         isolate_description=""):
    """Synthesize the experiment chain down to a Sample, reading
    reference/date/type hints from the GenomeDiff header (no breseq HTML).

    `ale_number` and `isolate_number` are text and the other two are integers, which is the
    shape `sample_names.parse_sample_identity` answers in and the shape of the columns.

    **The replicate is part of the label, not a level.** `3-30000-1-1` and `3-30000-1-2` are
    two samples under one flask called `1-1` and `1-2`; they used to be one isolate with two
    replicate rows under it. The suffix is kept even when the replicate is 1, so an A-F-I-R
    name always yields the label the filename spelled -- a conditional would make `1` and
    `1-2` siblings, which reads as two unrelated samples rather than two replicates of one.
    """
    experiment = context["experiment"]
    metadata = document.metadata

    # The `.gd`'s REFSEQ header names the *genome*, and lands on the sample's
    # `sequencing` group.
    # It shares no spelling with `Mutation.seq_id`, which is a contig within that genome --
    # both were called `seq_id` until the column was renamed.
    reference_genome = metadata.get("REFSEQ", "") or ""
    sequencing_date = metadata.get("CREATED", "") or ""
    # breseq marks a polymorphism run with -p. That is the *mixed* case, so the stored
    # flag is its negation -- the one place in the suite that turns the .gd into polarity.
    is_clonal = " -p" not in (metadata.get("COMMAND", "") or "")

    ale_id, _ = Population.objects.get_or_create(experiment=experiment, name=ale_number)
    label = sample_names.sample_label(isolate_number, tech_rep_number)
    seq_experiment, _ = Sample.objects.get_or_create(
        population=ale_id,
        time_point=flask_number,
        name=label,
        defaults={
            "source_name": sample_name,
            "is_clonal": is_clonal,
            # The `[:200]` slices that stood on the next two are gone with the columns they
            # were cut to fit; the values are stored as the header gave them.
            "supplemental_data": {Sample.COMPONENT: {
                Sample.SEQUENCING: {"date": sequencing_date,
                                    "reference_genome": reference_genome},
            }},
            # A label to read the sample by, on creation only: `label`
            # prefers it, so `Ara-2_500gen_763A` shows as itself rather than as
            # `AAra-2 F500 I763A`. Not part of the identity -- a sample found by its
            # coordinate keeps whatever description it was given, including a hand-edited
            # one.
            "description": isolate_description[:300],
        })
    return seq_experiment


def _get_or_create_autonumbered_chain(context, document, sample_name):
    """Chain for a sample whose filename carries no identity at all.

    Everything hangs off ALE 1 / time point 1, but each distinct sample gets its own label so
    the samples stay individually addressable. Re-importing a sample must not allocate a
    second one, so an existing sample of this name is reused."""
    experiment = context["experiment"]

    existing = Sample.objects.filter(
        source_name=sample_name,
        **{paths.to_experiment(): experiment}).first()
    if existing is not None:
        return existing

    metadata = document.metadata
    ale_id, _ = Population.objects.get_or_create(experiment=experiment, name="1")

    return Sample.objects.create(
        population=ale_id,
        time_point=1,
        name=_next_sample_number(ale_id, 1),
        # label() prefers the description, so this is what makes the
        # sample show up as "Ara-1_500gen_762B" rather than a generic "A1 F1 I3".
        description=sample_name[:300],
        is_clonal=" -p" not in (metadata.get("COMMAND", "") or ""),
        source_name=sample_name,
        supplemental_data={Sample.COMPONENT: {
            Sample.SEQUENCING: {"date": metadata.get("CREATED", "") or "",
                                "reference_genome": metadata.get("REFSEQ", "") or ""},
        }})


def _next_sample_number(population, time_point):
    """The next free number at this coordinate, as text.

    Counted in Python rather than by `Max("name")`, which stopped meaning anything when the
    column became text (`aledb_experiment.0008`): `MAX` over strings answers `"9"` for a
    coordinate holding 1..10, and the next sample would collide with 10. Labels that are not
    numbers are skipped rather than counted -- a sample called `763A`, or one called `1-2`,
    says nothing about which numbers are free.

    It took a `TimePoint` row and takes the pair that replaced it. Same question either way:
    the coordinate is what a name has to be unique within.
    """
    numbers = [int(value) for value
               in Sample.objects.filter(population=population, time_point=time_point)
                                .values_list("name", flat=True)
               if str(value).isdigit()]
    return str(max(numbers, default=0) + 1)


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
               experiment.id if experiment is not None else "<id>"))


def _database_gd_mutations(seq_experiment, document, experiment=None):
    """Create Mutation + MutationCall rows from the parsed mutations.

    Re-importing the same sample is idempotent: existing MutationCalls for this
    Sample are cleared first, and Mutations are deduplicated via
    get_or_create (per experiment).

    When the experiment has a reference, the records are annotated first, so gene,
    codon and amino-acid fields come from the reference rather than from whatever
    the .gd happened to carry. A .gd dropped before any reference simply imports
    unannotated; `./aledb reannotate` fills it in once one arrives."""
    MutationCall.objects.filter(sample=seq_experiment).delete()

    records = [record for record in document.mutations if _is_storable(record)]
    verbatim = [{
        "type": record.type,
        "id": record.id,
        "parent_ids": record.parent_ids,
        **dict(record.attributes),
    } for record in records]

    # Annotate copies: the stored record is contractually verbatim, and to_gd_line() splats
    # every key of it onto the line it emits for gdtools APPLY.
    annotated = [dict(entry) for entry in verbatim]
    if experiment is not None:
        annotation.annotate_records(annotated, experiment)

    mutation_calls = []
    for record, genome_diff, annotated_record in zip(records, verbatim, annotated):
        attributes = dict(record.attributes)
        gene_list = get_annotated_gene_list(
            annotated_record.get("gene_name") or attributes.get("gene_name"),
            annotated_record.get("gene_product") or attributes.get("gene_product"))
        gene_str = ", ".join(gene_list)
        sequence_change = synthesize_sequence_change(record)[:200]

        mutation, created = Mutation.objects.get_or_create(
            experiment=experiment,
            # The record's `position` is the mutation's start; `mutation_interval`
            # returns exactly this for every type we store, so there is nothing to
            # derive and the column can be NOT NULL from creation.
            start_position=attributes.get("position"),
            seq_id=attributes.get("seq_id"),
            mutation_type=record.type,
            feature_length=attributes.get("size"),
            sequence_change=sequence_change,
            gene=gene_str,
            defaults={
                "supplemental_data": Mutation.genome_diff_container(genome_diff),
                "product": attributes.get("gene_product") or "",
                "protein_change": "",
            })
        if not created and not mutation.genome_diff:
            # Backfill a pre-existing (e.g. CLI-imported) row so it becomes APPLY-complete.
            # Through `set_record`, which merges: the row may already carry another
            # component's import record, and a blind assignment would drop it.
            mutation.set_record(Mutation.COMPONENT, Mutation.GENOME_DIFF, genome_diff)
        annotation.apply_to(mutation, annotated_record)

        mutation_calls.append(MutationCall(
            sample=seq_experiment,
            mutation=mutation,
            present=True,
            source=BRESEQ_SOURCE,
            frequency=_coerce_frequency(attributes.get("frequency"))))

    MutationCall.objects.bulk_create(mutation_calls)
    _database_uncalled_regions(seq_experiment, document)
    return len(mutation_calls)


def _database_uncalled_regions(seq_experiment, document):
    """Record the regions a .gd's MC (missing coverage) evidence says were not called.

    aledb_stats counts them per sample, and aledb-phylogeny reads them to mark a site
    *ambiguous* rather than ancestral -- so a sample missing these does not merely lose a
    number on a page, it starts asserting that mutations are absent where nothing is known.

    Only the breseq-directory CLI path used to write them, so a web-imported sample had
    none; both paths go through here now.
    """
    UncalledRegion.objects.filter(sample=seq_experiment).delete()
    for record in document.evidence:
        if record.type != "MC":
            continue
        attributes = record.attributes
        # `start`/`end` are integer columns, and genomediff already parses an MC record's
        # as ints -- but it leaves anything it cannot read as the raw string, and a file
        # that hands us one of those must not fail the whole import. The sample keeps its
        # mutations and loses one region, which is the same bargain coverage derivation
        # makes; a region nobody can place is not one worth storing.
        try:
            start, end = int(attributes.get("start")), int(attributes.get("end"))
        except (TypeError, ValueError):
            logger.warning("skipping an MC record with unreadable bounds (%r..%r) in %s",
                           attributes.get("start"), attributes.get("end"),
                           seq_experiment.source_name)
            continue
        UncalledRegion.objects.get_or_create(
            seq_id=attributes.get("seq_id"),
            start=start,
            end=end,
            sample=seq_experiment)


def export_gd_text(seq_experiment):
    """Reconstruct a GenomeDiff (.gd) file for a Sample's mutations.

    The result is a valid ``.gd`` accepted by ``gdtools APPLY`` (resolution of MOB
    ``repeat_name`` / CON/INT ``region`` still requires the reference genbank named
    in ``#=REFSEQ``)."""
    lines = ["#=GENOME_DIFF\t1.0"]
    reference_genome = seq_experiment.sequencing.get("reference_genome") or ""
    if reference_genome:
        lines.append("#=REFSEQ\t%s" % reference_genome)

    calls = (MutationCall.objects
                .filter(sample=seq_experiment)
                .select_related("mutation")
                .order_by("mutation__start_position"))
    for mutation_call in calls:
        gd_line = mutation_call.mutation.to_gd_line()
        if gd_line:
            lines.append(gd_line)
    return "\n".join(lines) + "\n"


def synthesize_sequence_change(record):
    """Build a short human-readable allele description used for display and as the
    dedup discriminator (the discrete alleles themselves live in the stored record).

    Public because `aledb_mutation_editor` builds mutations by hand and has to land on the
    same string this does. `sequence_change` is one of the seven fields
    `Mutation.objects.get_or_create` keys on below, so a second rule for it would let a
    hand-entered mutation and a later re-import of the same call become two rows.
    """
    mutation_type = record.type

    def field(name, default=None):
        return _plain(record.get(name, default))

    if mutation_type in ("SNP", "INS", "SUB"):
        return str(field("new_seq", ""))
    if mutation_type == "DEL":
        return "del %s bp" % field("size", "?")
    if mutation_type == "MOB":
        sign = "+" if field("strand") == 1 else "-"
        return "%s (%s) +%s bp" % (field("repeat_name", ""), sign, field("duplication_size", 0))
    if mutation_type == "AMP":
        return "%s bp x%s" % (field("size", "?"), field("new_copy_number", "?"))
    if mutation_type == "INV":
        return "inv %s bp" % field("size", "?")
    if mutation_type in ("CON", "INT"):
        return str(field("region", ""))
    return " ".join(
        str(field(name))
        for name in TYPE_SPECIFIC_FIELDS.get(mutation_type, ())
        if field(name) is not None)


def _plain(value):
    """A parsed number as its own value, independent of how the file spelled it.

    genomediff returns `PreservedInt`/`PreservedFloat` for values whose source text would not
    format back identically -- `size=0042`, `frequency=8.39314286e-01` -- and their `__str__`
    answers that original text. That is right for writing a `.gd` back out and wrong here:
    `sequence_change` is one of the seven fields `Mutation.objects.get_or_create` keys on, so
    formatting from source text would fork one mutation into two rows depending on how each
    file happened to write the number.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def _coerce_frequency(value):
    """MutationCall.frequency is a float; default clonal 1.0.

    `float(value)` rather than `float(str(value))`: genomediff hands back `PreservedFloat` /
    `PreservedInt` for numbers whose source text would not format back identically, and those
    subclass `float` / `int` -- so this reaches the *parsed* number directly and needs none of
    the detour through source text that `_plain` exists to undo.
    """
    if value is None:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def run_post_processing(experiment):
    """Recompute derived data so imported mutations surface everywhere the CLI
    path's do (filters, plugin rebuilds, stats, dashboard).

    Public because re-annotation needs it too: changing a mutation's annotation
    changes what convergence, fixation and the dashboard counts see.

    This was four statements naming four things by hand -- the filter row, the plugin hooks,
    the needle-plot data and the dashboard -- and adding a fifth meant editing this function,
    which is what kept core's derived data reachable only from the import path. The list lives
    in `aledb_common.rebuild_registry` now and this asks for all of it, which is also why
    nothing had to be removed here when the needle plot stopped being stored: a rebuild that
    no longer exists is simply not in the registry to be run.

    Eager, and marked stale first. Eager because an import is already a long operation and
    its whole point is to leave the data queryable, so the Overview should be warm when it
    finishes rather than making its first reader wait. Marked stale first so that a rebuild
    which fails is recorded as still needing to run -- the old code left no trace of that at
    all, and a plugin hook that raised took the whole import down with it."""
    from aledb_common.rebuild_registry import request_rebuild, run_rebuilds

    request_rebuild(experiment.id, reason='experiment data imported')
    run_rebuilds(experiment.id)

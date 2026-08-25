"""The import types aledb-core itself provides.

Each is registered with ``aledb_common.import_registry`` from ``ImportConfig.ready()``, exactly
as a plugin would register its own -- so these three have no privileged status beyond running
first by ``priority``.

The functions here are thin: detection and ingest already exist in ``breseq_folder``,
``reference``, and ``gd_import``. This module only gives them the uniform
``(experiment, staged_root, paths, user)`` shape the registry calls.
"""

import logging
import os

from django.db import transaction

from aledb_common.import_registry import (
    PRIORITY_DATA,
    PRIORITY_REFERENCE,
    matches_patterns,
    register_import_handler,
)

logger = logging.getLogger("aledb_import.handlers")

REFERENCE_PATTERNS = [
    ".gbk", ".gb", ".gbff", ".genbank",
    ".gff", ".gff3",
    ".fa", ".fasta", ".fna", ".fas",
]

BRESEQ_PATTERNS = [
    # Everything a sample contributes lives in data/ -- the .gd beside the reference it
    # was called against. output/ is not consulted, and annotated.gd is not read at all
    # now that annotation comes from the stored reference. See aledb_import.breseq_folder.
    "data/output.gd",
    # ~10 KB, and the only source of the sample's read/coverage statistics now that they are
    # no longer scraped out of summary.html. Optional: a sample without it still imports.
    "data/summary.json",
    "data/reference.gff3",
    "data/reference.fasta",
    "data/reference.bam",
    "data/reference.bam.bai",
]

# replace_annotation takes features, not sequence, so a FASTA has nothing it can use.
# FASTA is included even though a FASTA carries no annotation to install: this type is also
# how an experiment's contigs get renamed, and a rename with no annotation change is a
# legitimate thing to arrive holding. A FASTA whose sequence and names both match is simply a
# no-op, which is the same answer the GenBank of an unchanged genome already gets.
ANNOTATION_PATTERNS = [".gbk", ".gb", ".gbff", ".genbank", ".gff", ".gff3",
                       ".fa", ".fasta", ".fna", ".fas"]

GENOMEDIFF_PATTERNS = [".gd"]


# --- breseq result folders ----------------------------------------------------------------

def detect_breseq_folders(staged_root, paths):
    """Claim every file belonging to a directory that looks like a breseq sample.

    Directory-shaped rather than suffix-shaped, which is why this handler supplies its own
    detect: a sample is a directory containing data/output.gd, and
    the reference and BAM beside it belong to that sample rather than to the reference or
    genomediff handlers.
    """
    from aledb_import.breseq_folder import find_sample_dirs

    sample_dirs = find_sample_dirs(staged_root)
    if not sample_dirs:
        return []
    prefixes = tuple(
        os.path.relpath(d, staged_root) + os.sep for d in sample_dirs)
    return [p for p in paths
            if p.startswith(prefixes) and matches_patterns(p, BRESEQ_PATTERNS)]


def handle_breseq_folders(experiment, staged_root, paths, user):
    from aledb_import.breseq_folder import import_samples_into

    return import_samples_into(experiment, staged_root, person=_person(user))


# --- reference genomes --------------------------------------------------------------------

def detect_annotation(staged_root, paths):
    """As detect_reference, but only files that carry annotation."""
    inside_a_sample = set(detect_breseq_folders(staged_root, paths))
    return [p for p in paths
            if p not in inside_a_sample and matches_patterns(p, ANNOTATION_PATTERNS)]


def detect_reference(staged_root, paths):
    """Reference files at the drop root, never ones inside a breseq sample.

    A breseq sample carries data/reference.fasta and data/reference.gff3, which match the
    reference extensions. Those belong to their sample -- the breseq handler hash-checks them
    against the experiment -- so claiming them here would both steal them from that check and
    report them as separate files.
    """
    inside_a_sample = set(detect_breseq_folders(staged_root, paths))
    return [p for p in paths
            if p not in inside_a_sample and matches_patterns(p, REFERENCE_PATTERNS)]


def handle_reference(experiment, staged_root, paths, user, options=None):
    return _ingest_reference(experiment, staged_root, paths, annotation_only=False,
                             options=options)


def handle_replace_annotation(experiment, staged_root, paths, user, options=None):
    """Refresh an established reference's gene annotation, leaving its sequence alone.

    The narrow half of what the retired `/import/reference/` page could do. That page also
    accepted `replace=True`, which overwrote a *different* genome; this deliberately does not.
    Sameness of sequence is the invariant every sample is hash-checked against, so a UI that
    can quietly break it is worse than no UI at all -- `reference_store.establish_or_check`
    still takes `replace=` for a shell operator who genuinely needs it.
    """
    return _ingest_reference(experiment, staged_root, paths, annotation_only=True,
                             options=options)


def _rename_payload(experiment, plan, filename):
    """What the user is being asked to agree to, in their terms.

    The re-import warning is the consequence most likely to be discovered months later:
    `gd_import._check_seq_ids` refuses a `.gd` whose seq_ids are not the reference's, so the
    original breseq folders stop being re-importable the moment their contigs are renamed.
    """
    from aledb_seq.models import Mutation, ResequencingExperiment

    counts = {
        "mutations": Mutation.objects.filter(
            ale_experiment=experiment,
            reseq_reference__in=list(plan.mapping)).count(),
        "samples": ResequencingExperiment.objects.filter(
            tech_rep__isolate__flask__ale_id__ale_experiment=experiment).count(),
        "alignments": ResequencingExperiment.objects.filter(
            tech_rep__isolate__flask__ale_id__ale_experiment=experiment,
            bam_stored=True).count(),
    }
    payload = dict(plan.as_payload(counts), kind="rename", file=filename)
    payload["message"] = (
        "%s is this experiment's genome under different contig names." % filename)
    payload["warnings"] = [
        "Stored alignments keep their current contig names and go on working -- igv is given "
        "an alias table so reads and coverage still resolve.",
        "Re-importing these samples from their original breseq result folders will be "
        "refused afterwards: their data/output.gd still names the old contigs.",
    ]
    return payload


def _ingest_reference(experiment, staged_root, paths, annotation_only, options=None):
    from aledb_common.import_registry import ConfirmationRequired
    from aledb_import import reference as reference_io
    from aledb_import import reference_store

    if annotation_only and not reference_store.has_reference(experiment):
        return {"files": [{"file": p, "mutations": 0,
                           "error": ("this experiment has no reference genome yet; set the "
                                     "sequence first, then replace its annotation")}
                          for p in paths],
                "total_mutations": 0}

    results = []
    for relative in paths:
        full = os.path.join(staged_root, relative)
        try:
            gff3_text, sequences = reference_io.normalize_reference(
                full, os.path.basename(relative))
            reference_store.establish_or_check(
                experiment, gff3_text, sequences, update_annotation=True,
                allow_rename=bool((options or {}).get("confirm_rename")))
            results.append({"file": relative, "mutations": 0, "error": None})
        except reference_store.RenameRequired as ask:
            # Not an error and not a per-file result: the same genome arrived under different
            # contig names, and renaming rewrites every mutation in the experiment. It
            # propagates out of run_import as a question about the whole drop.
            raise ConfirmationRequired(_rename_payload(experiment, ask.plan, relative))
        except reference_store.ReferenceMismatch:
            # Named separately from the blanket handler below: this is the one failure a user
            # can act on, and the hash-vs-hash text establish_or_check raises does not say so.
            logger.info("annotation replacement refused for %s: sequence differs", relative)
            results.append({"file": relative, "mutations": 0,
                            "error": ("the sequence in this file is not this experiment's "
                                      "reference genome; annotation can only be replaced "
                                      "for the same sequence")})
        except Exception as exc:
            logger.exception("reference import failed for %s", relative)
            results.append({"file": relative, "mutations": 0, "error": str(exc)})
    return {"files": results, "total_mutations": 0}


# --- bare GenomeDiff files ----------------------------------------------------------------

def detect_genomediff(staged_root, paths):
    """Only .gd files that are not part of a breseq sample.

    Without this the breseq folder's own data/output.gd would be claimed twice. The
    registry hands each file to the first handler that claims it, and breseq folders run at the
    same priority, so this exclusion has to be explicit rather than relying on ordering.
    """
    claimed_by_breseq = set(detect_breseq_folders(staged_root, paths))
    return [p for p in paths
            if p not in claimed_by_breseq and matches_patterns(p, GENOMEDIFF_PATTERNS)]


def handle_genomediff(experiment, staged_root, paths, user):
    from aledb_import.gd_import import (
        _database_gd_mutations,
        _parse_document,
        import_document_as_sample,
    )
    from aledb_import.gd_import import prepare_experiment_by_id
    from aledb_import.reference_store import has_reference

    person = _person(user)
    context = prepare_experiment_by_id(experiment.ale_id)

    if not has_reference(experiment):
        # A .gd carries no reference, so it cannot establish the one every sample shares.
        # In a mixed drop the reference handler has already run (lower priority), so reaching
        # here means none was supplied.
        return {"files": [{"file": p, "mutations": 0,
                           "error": ("this experiment has no reference genome; add one "
                                     "(GenBank, GFF3 or FASTA) before importing .gd files")}
                          for p in paths],
                "total_mutations": 0}

    results = []
    total = 0
    for relative in paths:
        filename = os.path.basename(relative)
        sample_name = filename[:-3] if filename.lower().endswith(".gd") else filename
        try:
            with transaction.atomic():
                with open(os.path.join(staged_root, relative), "rb") as handle:
                    document = _parse_document(handle)
                _, count = import_document_as_sample(
                    document, sample_name, context, person)
            results.append({"file": filename, "mutations": count, "error": None})
            total += count
        except Exception as exc:
            logger.exception("genomediff import failed for %s", relative)
            results.append({"file": filename, "mutations": 0, "error": str(exc)})

    if total:
        # Recompute what the new mutations changed: the experiment filter, the plugin
        # rebuilds (fixation, convergence), static stats and the dashboard.
        #
        # This was missing, so **no .gd imported through the web ever triggered any of
        # it** -- Fixed Mutations and Converged Mutations stayed empty however good the
        # data was, and the stats were whatever the last CLI upload left behind. The CLI
        # path (`gd_import.import_genomediffs`) and the breseq-folder handler both do this;
        # only this one did not, which is why the gap was invisible.
        #
        # Once, after all files, not once per file: the rebuilds are whole-experiment and
        # fixation compares an ALE's last two flasks, which is not knowable until every
        # sample is in. A drop that also carried breseq folders runs it a second time from
        # that handler -- idempotent, and cheaper than teaching the two to coordinate.
        from aledb_import.gd_import import run_post_processing

        run_post_processing(experiment)

    return {"files": results, "total_mutations": total}


def _person(user):
    return user.get_username() if user and user.is_authenticated else ""


def register_core_import_handlers():
    register_import_handler(
        name="reference",
        label="Reference genome (GenBank / GFF3 / FASTA)",
        patterns=REFERENCE_PATTERNS,
        priority=PRIORITY_REFERENCE,
        detect=detect_reference,
        handle=handle_reference,
        accepts_options=True,
        # Establishing a reference is a one-time act. Once the experiment has one,
        # offering this again invites the two things it will not do: replacing the
        # annotation (that is replace_annotation) and swapping in a different genome
        # (deliberately shell-only). Auto-detect still routes a reference dropped
        # alongside data, which is how a first drop establishes one.
        only_without_reference=True,
        description="Sets the reference every sample in the experiment is checked against.")
    register_import_handler(
        name="replace_annotation",
        label="Replace annotation or rename contigs (GenBank / GFF3 / FASTA)",
        patterns=ANNOTATION_PATTERNS,
        # Deliberately one step behind `reference`, which claims the same files: in
        # auto-detect the lower priority takes them all and this one claims nothing, so it is
        # reachable only by being named explicitly. That is what makes it a special option
        # rather than a second thing that fires whenever a GenBank is dropped.
        priority=PRIORITY_REFERENCE + 1,
        detect=detect_annotation,
        handle=handle_replace_annotation,
        accepts_options=True,
        requires_reference=True,
        description="Refresh the gene annotation from a new GenBank or GFF3. The sequence "
                    "must be identical; only the features are replaced.")
    register_import_handler(
        name="breseq_folder",
        label="breseq data folders",
        patterns=BRESEQ_PATTERNS,
        priority=PRIORITY_DATA,
        detect=detect_breseq_folders,
        handle=handle_breseq_folders,
        description="One or more sample folders, each with a data/ holding output.gd, the reference and the alignment.")
    register_import_handler(
        name="genomediff",
        label="GenomeDiff mutations (.gd)",
        patterns=GENOMEDIFF_PATTERNS,
        priority=PRIORITY_DATA + 10,
        detect=detect_genomediff,
        handle=handle_genomediff,
        # Shown but greyed out until there is a reference, rather than hidden: a bare
        # .gd is a thing people will arrive holding, and the useful answer is "get a
        # reference in first", not a dropdown that silently lacks the entry.
        requires_reference=True,
        unavailable="disable",
        description="Mutations only. Needs the experiment to already have a reference.")

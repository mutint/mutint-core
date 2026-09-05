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

from aledb_common import import_progress
from aledb_import.retry import with_retry
from aledb_common.import_registry import (
    KIND_REFERENCE,
    PRIORITY_DATA,
    PRIORITY_REFERENCE,
    matches_patterns,
    register_import_handler,
)
from aledb_experiment import paths

logger = logging.getLogger("aledb_import.handlers")

REFERENCE_PATTERNS = [
    ".gbk", ".gb", ".gbff", ".genbank",
    ".gff", ".gff3",
    ".fa", ".fasta", ".fna", ".fas",
]

BRESEQ_PATTERNS = [
    # Everything a sample's *data* contributes lives in data/ -- the .gd beside the reference
    # it was called against. annotated.gd is not read at all now that annotation comes from
    # the stored reference. See aledb_import.breseq_folder.
    #
    # (This said "output/ is not consulted", and that stopped being true: breseq's HTML report
    # is kept now so a reader can see the evidence behind a call. It is claimed by
    # BRESEQ_DIRECTORIES below rather than from here, because no suffix describes it.)
    "data/output.gd",
    # ~10 KB, and the only source of the sample's read/coverage statistics now that they are
    # no longer scraped out of summary.html. Optional: a sample without it still imports.
    "data/summary.json",
    "data/reference.gff3",
    "data/reference.fasta",
    "data/reference.bam",
    "data/reference.bam.bai",
]

# breseq's own HTML report, claimed whole. It cannot be a pattern: breseq chooses the
# filenames inside it and they differ between releases -- 0.50 writes one self-contained
# `evidence.html` with the whole evidence tree zipped inside it, older ones write an
# `evidence/` directory of pages and images -- and "everything under this directory" is not a
# suffix. See `directories` on `register_import_handler`, and `store.sample_report_dir`.
BRESEQ_DIRECTORIES = ["output"]

# replace_annotation takes features, not sequence, so a FASTA has nothing it can use.
# FASTA is included even though a FASTA carries no annotation to install: this type is also
# how an experiment's contigs get renamed, and a rename with no annotation change is a
# legitimate thing to arrive holding. A FASTA whose sequence and names both match is simply a
# no-op, which is the same answer the GenBank of an unchanged genome already gets.
ANNOTATION_PATTERNS = [".gbk", ".gb", ".gbff", ".genbank", ".gff", ".gff3",
                       ".fa", ".fasta", ".fna", ".fas"]

GENOMEDIFF_PATTERNS = [".gd"]
VCF_PATTERNS = [".vcf", ".vcf.gz"]

# The one file that makes a directory a sample. `breseq_folder` owns the definition; this is
# the suffix form of it, for reading sample names back off a list of claimed paths.
GD_RELATIVE_SUFFIX = "data/output.gd"

# Where each type sits in the Add page's dropdown, which is deliberately not the order they
# run in -- see `menu_order` on `register_import_handler`. Mutations first because that is
# what people come here for; replacing an established genome's annotation last because it is
# the rarest and the hardest to undo.
MENU_GENOMEDIFF = 10
MENU_VCF = 15
MENU_BRESEQ = 20
MENU_REFERENCE = 30
MENU_REPLACE_ANNOTATION = 90


# --- breseq result folders ----------------------------------------------------------------

def _under_output(path, prefixes):
    """Whether `path` is inside a breseq sample's own `output/` directory."""
    lowered = path.replace(os.sep, "/").lower()
    return any(lowered.startswith((prefix.replace(os.sep, "/") + "output/").lower())
               for prefix in prefixes)


def breseq_sample_prefixes(staged_root):
    """Path prefixes of every breseq sample directory in the drop."""
    from aledb_import.breseq_folder import find_sample_dirs

    return tuple(os.path.relpath(d, staged_root) + os.sep
                 for d in find_sample_dirs(staged_root))


def inside_a_breseq_sample(path, prefixes):
    """Whether `path` lives inside a breseq sample directory, whoever claims it.

    **Wider than "claimed by the breseq handler", and it has to be.** A breseq run writes more
    into `data/` than the six files that handler names: `annotated.gd`, and since 0.50
    `output.vcf`. Excluding only what breseq *claims* leaves those to be picked up by the
    genomediff and VCF handlers, which import the sample's own mutations a second time as
    phantom samples called `annotated` and `output`.

    Found by importing a real breseq folder. The `.gd` half of it predates the VCF importer;
    adding VCF made it happen on every breseq folder rather than only where an `annotated.gd`
    survived, which is what made it visible.

    The rule is the honest one either way: everything under a breseq sample directory belongs
    to that sample, and it is `breseq_folder`'s business what to read out of it.
    """
    return path.startswith(prefixes)


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
            if p.startswith(prefixes)
            and (matches_patterns(p, BRESEQ_PATTERNS) or _under_output(p, prefixes))]


def list_breseq_units(_staged_root, claimed):
    """One unit per sample directory, not per claimed file.

    A sample contributes five claimed paths -- the .gd, the reference pair and the BAM with
    its index -- so the default "one claimed file is one unit" would overstate the work
    fivefold and count nothing anybody is waiting on. The name must match what
    `breseq_folder._import_samples` reports, which is the directory's basename.

    **Derived from what was claimed, not from a second walk of the tree.** A sample *is* a
    directory holding `data/output.gd` and `detect_breseq_folders` has already found them
    all, so re-running `find_sample_dirs` here would traverse the whole staging area a
    second time to reach the same answer -- and the whole announcement is what the Add page
    waits on before it can draw a single row. Reading it off the claim is both quicker and
    one fewer opinion about which directories are samples.
    """
    names = []
    for path in claimed:
        if not path.replace(os.sep, "/").lower().endswith(GD_RELATIVE_SUFFIX):
            continue
        # "<anything>/<sample>/data/output.gd" -> "<sample>"
        sample_dir = os.path.dirname(os.path.dirname(path))
        names.append(os.path.basename(sample_dir.rstrip(os.sep)))
    return names


def handle_breseq_folders(experiment, staged_root, paths, user):
    from aledb_import.breseq_folder import import_samples_into

    return import_samples_into(experiment, staged_root, user=user)


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
    from aledb_sample.models import Mutation, Sample

    counts = {
        "mutations": Mutation.objects.filter(
            experiment=experiment,
            seq_id__in=list(plan.mapping)).count(),
        "samples": Sample.objects.filter(
            **{paths.to_experiment(): experiment}).count(),
        "alignments": Sample.objects.filter(
            **{paths.to_experiment(): experiment},
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
        refusals = [{"file": p, "mutations": None, "kind": KIND_REFERENCE,
                     "error": ("this experiment has no reference genome yet; set the "
                               "sequence first, then replace its annotation")}
                    for p in paths]
        for entry in refusals:
            import_progress.report(entry)
        return {"files": refusals, "total_mutations": 0}

    results = []
    for relative in paths:
        full = os.path.join(staged_root, relative)
        import_progress.begin(relative)
        try:
            gff3_text, sequences = reference_io.normalize_reference(
                full, os.path.basename(relative))
            reference_store.establish_or_check(
                experiment, gff3_text, sequences, update_annotation=True,
                allow_rename=bool((options or {}).get("confirm_rename")))
            entry = {"file": relative, "mutations": None,
                     "kind": KIND_REFERENCE, "error": None}
        except reference_store.RenameRequired as ask:
            # Not an error and not a per-file result: the same genome arrived under different
            # contig names, and renaming rewrites every mutation in the experiment. It
            # propagates out of run_import as a question about the whole drop.
            raise ConfirmationRequired(_rename_payload(experiment, ask.plan, relative))
        except reference_store.ReferenceMismatch:
            # Named separately from the blanket handler below: this is the one failure a user
            # can act on, and the hash-vs-hash text establish_or_check raises does not say so.
            logger.info("annotation replacement refused for %s: sequence differs", relative)
            entry = {"file": relative, "mutations": None,
                     "kind": KIND_REFERENCE,
                     "error": ("the sequence in this file is not this experiment's "
                               "reference genome; annotation can only be replaced "
                               "for the same sequence")}
        except Exception as exc:
            logger.exception("reference import failed for %s", relative)
            entry = {"file": relative, "mutations": None,
                     "kind": KIND_REFERENCE, "error": str(exc)}
        results.append(entry)
        import_progress.report(entry)
    return {"files": results, "total_mutations": 0}


# --- bare GenomeDiff files ----------------------------------------------------------------

def detect_genomediff(staged_root, paths):
    """Only .gd files that are not part of a breseq sample.

    Without this the breseq folder's own data/output.gd would be claimed twice. The
    registry hands each file to the first handler that claims it, and breseq folders run at the
    same priority, so this exclusion has to be explicit rather than relying on ordering.

    Excluded by *directory*, not by what breseq claims: `data/annotated.gd` sits beside
    `output.gd` and no breseq pattern names it, so a claim-based exclusion imported it as a
    phantom sample called `annotated`. See `inside_a_breseq_sample`.
    """
    prefixes = breseq_sample_prefixes(staged_root)
    return [p for p in paths
            if not inside_a_breseq_sample(p, prefixes)
            and matches_patterns(p, GENOMEDIFF_PATTERNS)]


def handle_genomediff(experiment, staged_root, paths, user):
    from aledb_import.gd_import import (
        _database_gd_mutations,
        _parse_document,
        import_document_as_sample,
    )
    from aledb_import.gd_import import prepare_experiment_by_id
    from aledb_import.reference_store import has_reference

    context = prepare_experiment_by_id(experiment.id)

    if not has_reference(experiment):
        # A .gd carries no reference, so it cannot establish the one every sample shares.
        # In a mixed drop the reference handler has already run (lower priority), so reaching
        # here means none was supplied.
        refusals = [{"file": os.path.basename(p), "mutations": 0,
                     "error": ("this experiment has no reference genome; add one "
                               "(GenBank, GFF3 or FASTA) before importing .gd files")}
                    for p in paths]
        for entry in refusals:
            import_progress.report(entry)
        return {"files": refusals, "total_mutations": 0}

    results = []
    total = 0
    for relative in paths:
        filename = os.path.basename(relative)
        sample_name = filename[:-3] if filename.lower().endswith(".gd") else filename
        import_progress.begin(filename)

        def import_one(relative=relative, sample_name=sample_name):
            with transaction.atomic():
                with open(os.path.join(staged_root, relative), "rb") as handle:
                    document = _parse_document(handle)
                return import_document_as_sample(
                    document, sample_name, context)

        try:
            # Retried only for lock contention, and safe to retry because the transaction
            # above rolls back whole and re-import is idempotent. See aledb_import.retry.
            _, count, replaced = with_retry(import_one, describe=filename)
            entry = {"file": filename, "mutations": count, "error": None,
                     "replaced": replaced}
            total += count
        except Exception as exc:
            logger.exception("genomediff import failed for %s", relative)
            entry = {"file": filename, "mutations": 0, "error": str(exc)}
        results.append(entry)
        import_progress.report(entry)

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

        import_progress.stage("Recomputing derived data…")
        run_post_processing(experiment)

    return {"files": results, "total_mutations": total}


def detect_vcf(staged_root, paths):
    """Only .vcf files that are not part of a breseq sample, mirroring detect_genomediff.

    breseq 0.50 writes `data/output.vcf` beside its `.gd`, so without the directory exclusion
    every breseq folder imported its own mutations twice -- once properly, and once as a
    sample named `output`. See `inside_a_breseq_sample`.
    """
    prefixes = breseq_sample_prefixes(staged_root)
    return [p for p in paths
            if not inside_a_breseq_sample(p, prefixes)
            and matches_patterns(p, VCF_PATTERNS)]


def list_vcf_units(staged_root, claimed):
    """One unit per sample the drop will produce, which is not one per file.

    A twelve-column VCF is twelve samples' worth of work, and a progress bar counting files
    would sit at 0/1 for the length of it. The announced name must equal the `file` key of the
    eventual result -- `test_import_progress.AnnouncedNamesTestCase` pins that -- so this reads
    each file's header to find out what the samples are called, and `handle_vcf` reports under
    exactly the same names.
    """
    from aledb_import import vcf, vcf_import

    names = []
    for relative in claimed:
        filename = os.path.basename(relative)
        try:
            with open(os.path.join(staged_root, relative), "rb") as handle:
                document = vcf.read(handle, filename)
        except Exception:
            # Unreadable here is reported by the handler, which is the one place that should
            # say so. Announce the file itself so the row exists to carry the error.
            names.append(filename)
            continue
        names.extend(vcf_import.sample_names_for(document, filename))
    return names


def handle_vcf(experiment, staged_root, paths, user):
    """Import variant calls, one ALEdb sample per VCF sample column."""
    from aledb_import import vcf, vcf_import
    from aledb_import.gd_import import prepare_experiment_by_id, run_post_processing
    from aledb_import.reference_store import has_reference

    context = prepare_experiment_by_id(experiment.id)

    if not has_reference(experiment):
        # A VCF carries no reference, and the reference is also what the REF check and MOB
        # inference read -- so this is refused for two reasons rather than one.
        refusals = [{"file": os.path.basename(p), "mutations": 0,
                     "error": ("this experiment has no reference genome; add one "
                               "(GenBank, GFF3 or FASTA) before importing VCF files")}
                    for p in paths]
        for entry in refusals:
            import_progress.report(entry)
        return {"files": refusals, "total_mutations": 0}

    results = []
    total = 0
    for relative in paths:
        filename = os.path.basename(relative)
        try:
            with open(os.path.join(staged_root, relative), "rb") as handle:
                document = vcf.read(handle, filename)
        except Exception as exc:
            logger.exception("could not read %s", relative)
            entry = {"file": filename, "mutations": 0, "error": str(exc)}
            results.append(entry)
            import_progress.begin(filename)
            import_progress.report(entry)
            continue

        for sample_name in vcf_import.sample_names_for(document, filename):
            import_progress.begin(sample_name)

            def import_one(sample_name=sample_name):
                with transaction.atomic():
                    return vcf_import.import_sample(
                        document, sample_name, context, experiment)

            try:
                count, replaced, problems = with_retry(import_one, describe=sample_name)
                entry = {"file": sample_name, "mutations": count, "error": None,
                         "warnings": problems, "replaced": replaced}
                total += count
            except Exception as exc:
                logger.exception("vcf import failed for %s in %s", sample_name, relative)
                entry = {"file": sample_name, "mutations": 0, "error": str(exc),
                         "warnings": []}
            results.append(entry)
            import_progress.report(entry)

    if total:
        import_progress.stage("Recomputing derived data\u2026")
        run_post_processing(experiment)

    return {"files": results, "total_mutations": total}


def register_core_import_handlers():
    register_import_handler(
        name="reference",
        label="Reference genome (GenBank / GFF3 / FASTA)",
        patterns=REFERENCE_PATTERNS,
        priority=PRIORITY_REFERENCE,
        detect=detect_reference,
        handle=handle_reference,
        accepts_options=True,
        menu_order=MENU_REFERENCE,
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
        # Last, deliberately. It rewrites the genome every sample in the experiment is
        # checked against, and is the rarest and least reversible thing on the menu.
        menu_order=MENU_REPLACE_ANNOTATION,
        description="Refresh the gene annotation from a new GenBank or GFF3. The sequence "
                    "must be identical; only the features are replaced.")
    register_import_handler(
        name="breseq_folder",
        label="breseq data folders",
        patterns=BRESEQ_PATTERNS,
        directories=BRESEQ_DIRECTORIES,
        priority=PRIORITY_DATA,
        detect=detect_breseq_folders,
        handle=handle_breseq_folders,
        list_units=list_breseq_units,
        menu_order=MENU_BRESEQ,
        description="One or more sample folders, each with a data/ holding output.gd, the "
                    "reference and the alignment. breseq's own output/ report is kept too.")
    register_import_handler(
        name="genomediff",
        label="GenomeDiff mutations (.gd)",
        patterns=GENOMEDIFF_PATTERNS,
        priority=PRIORITY_DATA + 10,
        detect=detect_genomediff,
        handle=handle_genomediff,
        # Reports a bare filename, not the path it was dropped under, so the announcement
        # has to say the same thing -- see `list_units` on `register_import_handler`.
        list_units=lambda _root, claimed: [os.path.basename(p) for p in claimed],
        # First in the menu. It runs last -- a .gd is hash-checked against a reference that
        # has to exist by then -- but it is the commonest thing anybody opens this page to
        # do, and it sat at the bottom for no reason other than that ordering.
        menu_order=MENU_GENOMEDIFF,
        # Absent until there is a reference. A bare .gd is a thing people will arrive
        # holding, so the answer they need -- get a reference in first -- is the page's
        # banner, which says exactly that and names .gd as the case it does not cover.
        # An entry you can see and cannot pick is a worse way to say the same thing.
        requires_reference=True,
        description="Mutations only. Needs the experiment to already have a reference.")
    register_import_handler(
        name="vcf",
        label="VCF variant calls (.vcf)",
        patterns=VCF_PATTERNS,
        # Beside genomediff: both are mutations against a reference that must already exist,
        # and neither can run before the reference handler.
        priority=PRIORITY_DATA + 10,
        detect=detect_vcf,
        handle=handle_vcf,
        list_units=list_vcf_units,
        menu_order=MENU_VCF,
        requires_reference=True,
        description="Variant calls from any caller. Normalized and converted to GenomeDiff "
                    "on the way in, so they share rows with breseq's own calls.")

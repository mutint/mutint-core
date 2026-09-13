"""An experiment as one folder: its reference, one mutation file per sample, and a manifest.

**Export experiment** writes it and the **MutInt Archive** import type reads it, so an
experiment can move from one MutInt to another as a folder drop -- or through `/api/`, which
serves the same zip for a public project. The layout is a drop the other import types could
read on their own, with a manifest beside it saying what they cannot:

    <experiment>/
      mutint.json                the manifest -- see `build_manifest`
      metadata.csv               where each sample sits, the way any drop says it
      reference/reference.gff3   the stored reference, byte for byte
      reference/reference.fasta
      samples/<name>.gd | .vcf   `export_gd_text` / `export_vcf_text` per sample

**It carries the mutations and the reference, and nothing derived from reads.** Alignments,
coverage and breseq's HTML report stay where they were made; they are gigabytes, and every
analysis MutInt does reads the mutations. A sample that came from a VCF travels as that VCF,
since the export is verbatim and the `.gd` path would lose its QUAL, FILTER and INFO.

**The manifest is authoritative on import.** The importer places every sample through the
usual seam -- the archive's own `metadata.csv` is parsed and installed for it, so placement
is one rule -- and then `apply_manifest` writes what no file format carries: the experiment's
name and notes, each population's species and strain, each sample's flags, description and
supplemental groups, the publications, and the designated ancestor, all overwriting the
target's. A lock is not carried, and nothing about the project is: the drop lands in an
experiment somebody already created inside a project they already own.

Written and read side by side, like `vcf_import` and `vcf_export`, so the two halves cannot
drift apart about what a key means.
"""

import csv
import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone

from django.db import transaction

from mutint_common import store
from mutint_experiment import paths
from mutint_experiment.models import Population
from mutint_import import metadata as sample_metadata
from mutint_import import reference_store
from mutint_import.gd_import import (
    UNSPECIFIED_POPULATION,
    export_gd_text,
    format_time_point,
)
from mutint_import.vcf_export import VCF_RECORD, export_vcf_text
from mutint_sample.flags import FLAG_FIELDS
from mutint_sample.models import ReferenceSequences, Sample

logger = logging.getLogger("mutint_import.archive")

MANIFEST = "mutint.json"
FORMAT = "mutint-archive"
VERSION = 1
REFERENCE_DIR = "reference"
SAMPLES_DIR = "samples"

#: The `mutint_core` groups a sample carries across. `vcf` and `genome_diff` are rebuilt by
#: the importer from the files themselves, which is what they describe.
SAMPLE_GROUPS = (Sample.SEQUENCING, Sample.CURATION, Sample.BRESEQ, Sample.INPUTS)


class ArchiveError(Exception):
    """The archive cannot be written or read; the message says why."""


_UNSAFE_IN_NAME = re.compile(r"[\s/\\]+")


def slug(name):
    """A name as a filename: whitespace and path separators become `_`, as
    `reference_export.filename` does for the reference download."""
    return _UNSAFE_IN_NAME.sub("_", (name or "").strip()) or "experiment"


def archive_filename(experiment):
    return slug(experiment.name) + ".zip"


# --- writing --------------------------------------------------------------------------------


def build_manifest(experiment):
    """What the archive says about the experiment, as the JSON `mutint.json` holds."""
    return _manifest_and_files(experiment)[0]


def _manifest_and_files(experiment):
    """The manifest, and `{sample pk: file}` for the writer.

    Samples are listed with the file each will be written to, so the manifest is the one
    mapping from file to sample and the importer never has to guess at a name; the ancestor
    is named by its file for the same reason. Populations listed are the ones holding
    samples, since a population with none has nothing to place.
    """
    from mutint_bibliome.models import Publication
    from mutint_common.version import __version__

    reference = ReferenceSequences.objects.filter(experiment=experiment).first()
    if reference is None:
        raise ArchiveError("%s has no reference genome, so there is nothing to export the "
                           "mutations against." % (experiment.name,))

    samples = list(Sample.objects
                   .filter(**{paths.to_experiment(): experiment})
                   .select_related("population")
                   .order_by("population__name", "time_point", "name", "pk"))
    files = _unique_files(samples)
    entries = [_sample_entry(sample, files[sample.pk]) for sample in samples]

    ancestor_file = files.get(experiment.ancestor_id) if experiment.ancestor_id else None
    populations = sorted({sample.population for sample in samples}, key=lambda p: p.name)

    return {
        "format": FORMAT,
        "version": VERSION,
        "mutint_version": __version__,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment": {
            "name": experiment.name,
            "date": experiment.date.isoformat() if experiment.date else None,
            "notes": experiment.notes or "",
            "ancestor": ancestor_file,
        },
        "reference": {
            "gff3": "%s/%s" % (REFERENCE_DIR, store.REFERENCE_GFF3),
            "fasta": "%s/%s" % (REFERENCE_DIR, store.REFERENCE_FASTA),
            "gff3_sha256": reference.gff3_sha256,
            "fasta_sha256": reference.fasta_sha256,
            "sequence_sha256": reference.sequence_sha256,
            "seq_ids": list(reference.seq_ids or []),
        },
        "populations": [{
            "name": population.name,
            "description": population.description or "",
            "species": population.species or "",
            "strain": population.strain or "",
        } for population in populations],
        "samples": entries,
        "publications": [{"url": publication.url, "title": publication.title}
                         for publication in Publication.objects
                         .filter(experiment=experiment).order_by("pk")],
    }, files


def _sample_entry(sample, file):
    entry = {
        "file": file,
        "format": "vcf" if file.endswith(".vcf") else "gd",
        "source_name": sample.source_name or "",
        "name": sample.name,
        "population": sample.population.name,
        "time_point": sample.time_point,
        "description": sample.description or "",
        "is_clonal": bool(sample.is_clonal),
        "supplemental_data": {group: sample.record(group)
                              for group in SAMPLE_GROUPS if sample.record(group)},
    }
    for field in FLAG_FIELDS:
        entry[field] = bool(getattr(sample, field))
    return entry


def _unique_files(samples):
    """`{sample pk: "samples/<stem>.<ext>"}`, one file per sample, no two alike.

    The stem is the sample's `source_name` -- what a re-import matches on -- which nothing
    makes unique: two multi-sample VCFs can both have a column called `S1`. A collision
    takes a `-2`, `-3` suffix, and the manifest is what maps the file back to the sample.
    """
    taken = set()
    files = {}
    for sample in samples:
        extension = ".vcf" if sample.record(VCF_RECORD) else ".gd"
        stem = slug(sample.source_name or "sample_%d" % sample.pk)
        candidate, counter = stem, 1
        while (candidate + extension).lower() in taken:
            counter += 1
            candidate = "%s-%d" % (stem, counter)
        taken.add((candidate + extension).lower())
        files[sample.pk] = "%s/%s%s" % (SAMPLES_DIR, candidate, extension)
    return files


METADATA_COLUMNS = ("sample", "population", "time_point", "sample_type", "data")


def metadata_csv(manifest):
    """The archive's `metadata.csv`, from the manifest's samples.

    Written so the other import types could place these files without the manifest, and
    read back by the archive importer itself so placement is `metadata.parse` in both
    directions. Population and time point go together or not at all, as that parser
    requires; an unplaced sample has neither.
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(METADATA_COLUMNS)
    for entry in manifest["samples"]:
        placed = entry.get("time_point") is not None
        writer.writerow([
            entry["name"],
            entry["population"] if placed else "",
            format_time_point(entry["time_point"]) if placed else "",
            "clone" if entry.get("is_clonal", True) else "population",
            os.path.basename(entry["file"]),
        ])
    return out.getvalue()


def write_archive(experiment, fileobj):
    """Write the experiment's archive as a zip to `fileobj`; returns the manifest."""
    manifest, files = _manifest_and_files(experiment)
    if reference_store.stored_reference(experiment.id) is None:
        raise ArchiveError("%s's reference files could not be read from the store."
                           % (experiment.name,))
    root = slug(experiment.name)
    samples = (Sample.objects.filter(pk__in=list(files))
               .select_related("population").order_by("pk"))

    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("%s/%s" % (root, MANIFEST),
                         json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        archive.writestr("%s/%s" % (root, sample_metadata.FILENAME), metadata_csv(manifest))
        for filename in (store.REFERENCE_GFF3, store.REFERENCE_FASTA):
            archive.write(store.experiment_reference_path(experiment.id, filename),
                          "%s/%s/%s" % (root, REFERENCE_DIR, filename))
        for sample in samples:
            file = files[sample.pk]
            text = export_vcf_text(sample) if file.endswith(".vcf") else export_gd_text(sample)
            archive.writestr("%s/%s" % (root, file), text)
    return manifest


def archive_bytes(experiment):
    buffer = io.BytesIO()
    write_archive(experiment, buffer)
    return buffer.getvalue()


# --- reading --------------------------------------------------------------------------------


def find_archive_roots(paths_in_drop):
    """The directories, relative to the drop, holding a `mutint.json` -- `""` for the drop
    itself. A dropped folder keeps its top directory, so the manifest is usually one level
    down; a drop of the folder's *contents* puts it at the root."""
    roots = set()
    for relative in paths_in_drop:
        if os.path.basename(relative) == MANIFEST:
            roots.add(os.path.dirname(relative))
    return sorted(roots)


def under(root, relative):
    if root == "":
        return True
    return relative.startswith(root + os.sep) or relative.startswith(root + "/")


def is_archive_zip(path):
    """Whether a zip on disk holds a `mutint.json` anywhere inside it."""
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return any(os.path.basename(name) == MANIFEST for name in archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def extract_zip(path, target):
    """Unpack an archive zip into `target`, refusing any member that would land outside it."""
    target = os.path.realpath(target)
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            destination = os.path.realpath(os.path.join(target, member.filename))
            if destination != target and not destination.startswith(target + os.sep):
                raise ArchiveError("%s contains a path outside the archive: %s"
                                   % (os.path.basename(path), member.filename))
        archive.extractall(target)


def zip_members(path):
    """The files inside an archive zip, relative to the zip, as the drop would list them."""
    with zipfile.ZipFile(path) as archive:
        return sorted(info.filename for info in archive.infolist() if not info.is_dir())


def read_manifest(path):
    """The manifest, checked for the shape this reader knows; `ArchiveError` otherwise."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise ArchiveError("%s could not be read: %s" % (MANIFEST, error))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ArchiveError("%s is not a MutInt archive manifest." % (MANIFEST,))
    version = manifest.get("version")
    if not isinstance(version, int) or version > VERSION:
        raise ArchiveError(
            "%s was written by a newer MutInt (archive version %s; this one reads up to %d)."
            % (MANIFEST, version, VERSION))
    for key, kind in (("experiment", dict), ("reference", dict), ("samples", list)):
        if not isinstance(manifest.get(key), kind):
            raise ArchiveError("%s has no %r section." % (MANIFEST, key))
    for entry in manifest["samples"]:
        for key in ("file", "name", "population"):
            if not isinstance(entry, dict) or not entry.get(key):
                raise ArchiveError("%s lists a sample without a %r." % (MANIFEST, key))
    manifest.setdefault("populations", [])
    manifest.setdefault("publications", [])
    return manifest


def placement_for(manifest):
    """The manifest's samples as the `metadata.Metadata` the import seam reads.

    Through `metadata.parse` on the same CSV text the archive carries, so the archive's
    `metadata.csv` and the placement the importer applies are one thing -- and so a manifest
    naming an impossible coordinate is refused with the parser's own sentence.
    """
    return sample_metadata.parse(metadata_csv(manifest), source=MANIFEST)


def find_imported(experiment, entry):
    """The sample a manifest entry landed as, by the coordinate it was placed at."""
    placed = entry.get("time_point") is not None
    return (Sample.objects
            .filter(population__experiment=experiment,
                    population__name=entry["population"] if placed else UNSPECIFIED_POPULATION,
                    time_point=entry["time_point"] if placed else None,
                    name=entry["name"])
            .select_related("population")
            .first())


@transaction.atomic
def apply_manifest(experiment, manifest, imported, user=None):
    """Write onto `experiment` everything the manifest says that the files could not.

    `imported` maps each manifest file to the `Sample` it became; a file that failed to
    import is absent and its entry is skipped. Every field the manifest carries replaces the
    target's: the archive is a copy of an experiment, and a copy that kept half of what it
    found would be neither. The ancestor is set last, by file, and cleared when the manifest
    says there is none -- and left alone when it says nothing, which an older manifest may.
    """
    from mutint_bibliome.models import Publication

    described = manifest["experiment"]
    if described.get("name"):
        experiment.name = described["name"]
    if "notes" in described:
        experiment.notes = described.get("notes") or ""
    experiment.save(update_fields=["name", "notes"])

    for described_population in manifest.get("populations") or []:
        if not described_population.get("name"):
            continue
        population, _ = Population.objects.get_or_create(
            experiment=experiment, name=described_population["name"])
        population.description = described_population.get("description") or ""
        population.species = described_population.get("species") or ""
        population.strain = described_population.get("strain") or ""
        population.save(update_fields=["description", "species", "strain"])

    for entry in manifest["samples"]:
        sample = imported.get(entry["file"])
        if sample is None:
            continue
        sample.source_name = entry.get("source_name") or sample.source_name
        sample.description = (entry.get("description") or "")[:300]
        sample.is_clonal = bool(entry.get("is_clonal", sample.is_clonal))
        for field in FLAG_FIELDS:
            if field in entry:
                setattr(sample, field, bool(entry[field]))
        groups = entry.get("supplemental_data") or {}
        for group in SAMPLE_GROUPS:
            if group in groups:
                sample.set_record(Sample.COMPONENT, group, groups[group], save=False)
        sample.save()

    if "publications" in manifest:
        Publication.objects.filter(experiment=experiment).delete()
        for publication in manifest["publications"]:
            if publication.get("url") or publication.get("title"):
                Publication.objects.create(
                    experiment=experiment, url=publication.get("url") or "",
                    title=publication.get("title") or "")

    if "ancestor" in described:
        ancestor = imported.get(described["ancestor"]) if described["ancestor"] else None
        if ancestor is not None:
            experiment.set_ancestor(ancestor, user)
        elif not described["ancestor"]:
            experiment.clear_ancestor()

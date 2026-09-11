"""Fetching sequencing reads from the SRA by accession, as FASTQ files on disk.

**ENA, over HTTPS, and no tool.** The Sequence Read Archive is mirrored by EBI's European
Nucleotide Archive, which does two things NCBI's own site does not: it answers a portal query
with one row per run -- whichever of run, sample, experiment or study accession was asked
about -- and it serves each run's reads as ordinary `fastq.gz` files over HTTPS, with an MD5
and a size beside each. So what a download costs is `requests`, which core already carries
for NCBI, and what it needs from the host is nothing. The alternative, `sra-tools`, would be a
conda package to provision, a `prefetch` cache to manage, and an SRA-to-FASTQ conversion that
runs for as long as the download did; it covers the runs ENA has not mirrored yet, or that
were submitted as alignments and have no FASTQ, and those are refused here with a sentence
naming the run rather than worked around.

**This is the reads counterpart of `ncbi_fetch`**, and copies its shape: `resolve` before
anything is written, so a typo costs one round trip; one `FetchError` for every failure,
because every caller shows the sentence; every `requests` exception turned into words through
`mutint_sample.ncbi.redact`, which is the stated rule for anything that puts a request failure
in front of a person even though ENA needs no key. What differs is where the download runs. A
reference is kilobytes and is fetched inside the request that finalizes the import; a run is
gigabytes and hours, so `download` is written to be called **from a background task**, inside
the job's log, with a cancellation poll -- and it is `mutint_breseq.tasks` that calls it, not
anything in core.

**Verification is the checksum, and a file that fails it is removed.** Each file is streamed
to `<name>.part`, hashed as it arrives, and renamed only when the MD5 and the size ENA promised
both match. A cancelled or failed download unlinks the part-file, so nothing that looks like a
whole read file is ever left where a tool could pick it up; completed files stay, since a
failed run keeps everything for diagnosis. There is no `Range` resume, deliberately: nothing
re-enters a failed run, and a cancelled one deletes its reads directory anyway, so resuming
would mean re-hashing a part-file for a case that does not occur.

**The caps are at resolve, where refusing costs nothing.** A study accession is one token
that can mean hundreds of runs, and `MUTINT_SRA_MAX_RUNS` and `MUTINT_SRA_MAX_BYTES` are what
stop a pasted BioProject from becoming a day of downloads and a hundred breseq runs; both
name themselves in the sentence so an operator who means it can raise them.
"""

import hashlib
import logging
import os
import time

import requests
from django.conf import settings

from mutint_import import sra
from mutint_import.accessions import AccessionError
from mutint_jobs.processes import CANCEL_POLL_SECONDS, Cancelled
from mutint_sample.ncbi import redact

logger = logging.getLogger("mutint_import.sra_fetch")

#: ENA's portal file report: one row per run for whatever accession is asked about.
FILEREPORT_URL = "https://www.ebi.ac.uk/ena/portal/api/filereport"

#: The columns asked for. `fastq_ftp`, `fastq_md5` and `fastq_bytes` are the download;
#: `sample_accession` is what a study is grouped by and `sample_alias` is what a sample is
#: then called (see `sra.samples_in`, `sra.sample_name_for`); the rest are shown to the
#: person deciding whether this is the run they meant.
FIELDS = ",".join((
    "run_accession", "sample_accession", "secondary_sample_accession",
    "experiment_accession", "study_accession", "sample_alias", "sample_title",
    "library_layout", "library_strategy", "instrument_platform", "read_count", "base_count",
    "fastq_ftp", "fastq_md5", "fastq_bytes",
))

#: Seconds between requests. The same value and reasoning as `ncbi_fetch`: nothing here is in
#: a hurry beside the download itself, and ENA publishes no limit to be exactly under.
POLITE_DELAY_SECONDS = 0.4

#: How much of a file is read between hash updates and cancellation checks.
CHUNK_BYTES = 1 << 20

#: What a file is called while it is arriving.
PART_SUFFIX = ".part"


class FetchError(Exception):
    """A fetch could not be completed, for a reason worth putting in front of a person.

    One type for every way this fails -- no such accession, no FASTQ, a checksum that did not
    match, no network -- because every caller does the same thing with it: shows the sentence.
    What distinguishes the cases is the sentence, and it always names the accession at fault.
    """


def timeout_seconds():
    """Seconds of *silence* a request may go before it is given up on.

    A `requests` timeout bounds each socket read, not the transfer, so this is not a limit on
    how long a multi-gigabyte file may take -- it is what turns a stalled mirror into a
    sentence instead of a worker held for ever.
    """
    return getattr(settings, "MUTINT_SRA_TIMEOUT", 60)


def max_runs():
    return getattr(settings, "MUTINT_SRA_MAX_RUNS", 50)


def max_bytes():
    return getattr(settings, "MUTINT_SRA_MAX_BYTES", 20_000_000_000)


# --- resolving ------------------------------------------------------------------------------

def resolve(tokens):
    """What each accession in `tokens` is, as `sra.Plan`s, without downloading a read.

    Raises `FetchError` (or `AccessionError`, for a token of no SRA shape) naming the first
    token that cannot be used. Everything a launch would later trip over is refused here,
    while the answer is still "fix the box": an accession ENA does not know, a run it holds
    no FASTQ for, one run reached by two tokens, and more runs or bytes than the caps allow.
    """
    plans = []
    seen = {}
    for index, token in enumerate(tokens):
        kind_name = sra.kind(token)
        if index:
            time.sleep(POLITE_DELAY_SECONDS)
        rows = _filereport(token)
        if not rows:
            raise FetchError(
                "%s is not a %s ENA knows of." % (token, sra.label(kind_name)))
        runs = []
        for row in rows:
            try:
                run = sra.run_from_row(row)
            except AccessionError as error:
                raise FetchError(str(error))
            if not run.files:
                raise FetchError(
                    "%s has no FASTQ files at ENA%s -- it may have been submitted as an "
                    "alignment, or not have been mirrored from the SRA yet."
                    % (run.accession, "" if run.accession == token else " (in %s)" % token))
            if run.accession in seen:
                # Downloading would write one filename twice, and in a mode that names a
                # sample per accession, two samples would be named for one set of reads.
                raise FetchError(
                    "%s is named twice, by %s and by %s."
                    % (run.accession, seen[run.accession], token))
            seen[run.accession] = token
            runs.append(run)
        plans.append(sra.Plan(token, kind_name, runs))

    total_runs = sum(len(plan.runs) for plan in plans)
    if total_runs > max_runs():
        raise FetchError(
            "That is %s runs, more than the %s one launch will fetch "
            "(MUTINT_SRA_MAX_RUNS)." % (format(total_runs, ",d"), format(max_runs(), ",d")))
    total_bytes = sum(plan.total_bytes for plan in plans)
    if total_bytes > max_bytes():
        raise FetchError(
            "That is %s of reads, past the %s this deployment will download in one launch "
            "(MUTINT_SRA_MAX_BYTES)." % (_human(total_bytes), _human(max_bytes())))
    return plans


def _filereport(token):
    """ENA's rows for `token`, or raise `FetchError`."""
    try:
        response = requests.get(
            FILEREPORT_URL,
            params={"accession": token, "result": "read_run", "fields": FIELDS,
                    "format": "json", "limit": "0"},
            timeout=timeout_seconds())
    except requests.RequestException as error:
        raise FetchError("Could not reach ENA to look up %s: %s" % (token, redact(error)))

    try:
        if response.status_code != 200:
            raise FetchError(
                "ENA answered HTTP %s looking up %s." % (response.status_code, token))
        try:
            rows = response.json()
        except ValueError:
            raise FetchError("ENA's answer about %s could not be read." % token)
    finally:
        response.close()
    if not isinstance(rows, list):
        raise FetchError("ENA's answer about %s could not be read." % token)
    return [row for row in rows if isinstance(row, dict)]


# --- downloading ----------------------------------------------------------------------------

def download(directory, plans, report=None, is_cancelled=None):
    """Download every file of `plans` into `directory`, verified.

    Returns `{filename: run_accession}` over every file written, which is what lets a caller
    tell the files it fetched from the ones somebody dropped beside them -- and record the
    run, rather than the files, as the sample's input (`mutint_sample.inputs.KIND_SRA`).

    `plans` are `sra.Plan`s or the dicts a consumer stored; `report` is an optional
    `callable(message)`, meant to be `logs.write` into the job's log so the person watching
    `/jobs/<pk>/log` sees which file is arriving; `is_cancelled` is an optional `callable()`
    asked between chunks, at most every `CANCEL_POLL_SECONDS`, and a True answer raises
    `mutint_jobs.processes.Cancelled` -- the class the run loop raises, so a task shelling out
    and a task downloading are cancelled the same way.

    The directory is created if it does not exist: a run whose reads are all fetched has had
    nothing else create it.
    """
    plans = sra.as_plans(plans)
    names = sra.filenames_by_run(plans)
    os.makedirs(directory, exist_ok=True)

    total = sum(len(run.files) for plan in plans for run in plan.runs)
    downloaded = {}
    position = 0
    for plan in plans:
        for run in plan.runs:
            for entry in run.files:
                position += 1
                if position > 1:
                    time.sleep(POLITE_DELAY_SECONDS)
                if report is not None:
                    report("Downloading %s (%s of %s, %s) from ENA…"
                           % (entry["name"], position, total, _human(entry.get("bytes") or 0)))
                _fetch_file(entry, os.path.join(directory, entry["name"]), run.accession,
                            is_cancelled)
                downloaded[entry["name"]] = names[entry["name"]]
    return downloaded


def _fetch_file(entry, path, accession, is_cancelled):
    """Stream one file to `path` through `path + PART_SUFFIX`, verifying it on the way."""
    part = path + PART_SUFFIX
    try:
        response = requests.get(entry["url"], stream=True, timeout=timeout_seconds())
    except requests.RequestException as error:
        raise FetchError(
            "Could not reach ENA to download %s: %s" % (entry["name"], redact(error)))
    if response.status_code != 200:
        response.close()
        raise FetchError(
            "ENA answered HTTP %s downloading %s." % (response.status_code, entry["name"]))

    digest = hashlib.md5()
    received = 0
    last_poll = time.monotonic()
    try:
        with open(part, "wb") as handle:
            for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                now = time.monotonic()
                if is_cancelled is not None and now - last_poll >= CANCEL_POLL_SECONDS:
                    last_poll = now
                    if is_cancelled():
                        raise Cancelled("cancelled while downloading %s" % entry["name"])
        if is_cancelled is not None and is_cancelled():
            raise Cancelled("cancelled while downloading %s" % entry["name"])
    except requests.RequestException as error:
        _discard(part)
        raise FetchError(
            "The download of %s stopped partway (%s)." % (entry["name"], redact(error)))
    except BaseException:
        _discard(part)
        raise
    finally:
        response.close()

    expected_bytes = entry.get("bytes") or 0
    if expected_bytes and received != expected_bytes:
        _discard(part)
        raise FetchError(
            "%s arrived %s long where ENA said %s -- the download was cut short, or the "
            "file has changed. Nothing was kept; try again."
            % (entry["name"], _human(received), _human(expected_bytes)))
    expected_md5 = (entry.get("md5") or "").lower()
    if expected_md5 and digest.hexdigest() != expected_md5:
        _discard(part)
        raise FetchError(
            "%s arrived with a different checksum from the one ENA published for %s. "
            "Nothing was kept; try again." % (entry["name"], accession))
    os.replace(part, path)


def _discard(part):
    try:
        os.unlink(part)
    except OSError:
        pass


def _human(size):
    """`size` in bytes as a person reads it, to one decimal past kilobytes."""
    if size < 1024:
        return "%d B" % size
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024 or unit == "TB":
            return "%.1f %s" % (size, unit)
    return "%d B" % size

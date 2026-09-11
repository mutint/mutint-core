"""Fetching a reference genome from NCBI by accession, as files to be imported.

Two databases and one download. A **nucleotide** accession (`NC_000913.3`, `U00096.3`) names a
record directly; a **genome** accession (`GCF_000005845.2`, `GCA_…`) names an assembly, which is
resolved to the nucleotide accessions it is made of and then fetched exactly like the first
kind. So there is one downloader, one parser and one shape of file arriving at the importer,
whichever page of NCBI's site somebody copied the accession off.

**This is not `mutint_sample.ncbi`, and lives here rather than beside it, for two reasons.**
That module says in as many words that it is eutils and nothing else and that a second database
is a sibling file; resolving an assembly is NCBI Datasets, a different API on a different host.
And its subject is *verification* -- being handed a sequence and a name and asking whether they
match -- while this module's subject is getting the bytes in the first place. What it borrows
from there is the client's manners: `request_params` identifies the tool and carries the API
key, `timeout_seconds` and `max_bases` are the same limits, and `redact` is what keeps the key
out of an error message.

**What comes back is written as GenBank, one file per accession somebody typed**, into an
`ncbi/` directory inside the staging area. An assembly's member sequences all go into that one
file, so one thing typed is one row in the import summary. From there they are ordinary staged
files: `mutint_import.handlers._ingest_reference` merges them with whatever was dropped exactly
as it already merges a chromosome in one upload and a plasmid in another. No import handler
knows this module exists.

**`rettype` is `gbwithparts` and must stay so.** A CON record -- an entry assembled from parts,
which is most RefSeq chromosomes -- comes back from a plain `gb` fetch with its features, a
LOCUS line stating its length, and *no ORIGIN block at all*. Biopython parses that quite
happily into a record with an empty sequence, so the reference would be established with the
right contig names and no bases. Verified both ways against the live service.
"""

import logging
import os
import re
import shutil
import time

import requests
from django.conf import settings

from mutint_import import reference as reference_io
from mutint_import.accessions import ASSEMBLY, MAX_SEQUENCES, database_name, kind
from mutint_sample.ncbi import (
    EFETCH_URL,
    Unreachable,
    max_bases,
    redact,
    request_params,
    summary,
    timeout_seconds,
)

logger = logging.getLogger("mutint_import.ncbi_fetch")

#: NCBI Datasets, the only way to ask what an assembly accession is made of. Used for that one
#: question; the sequences it names are downloaded through eutils like everything else.
DATASETS_SEQUENCE_REPORTS = (
    "https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession/%s/sequence_reports")

#: Reports per page. Datasets paginates, and a draft assembly runs to hundreds of scaffolds --
#: see `_sequence_reports`, where not following the pages would be a silently partial genome.
DATASETS_PAGE_SIZE = 1000

#: How many accessions go into one efetch. Batched rather than one each because an assembly is
#: many contigs and NCBI would rather be asked once.
FETCH_BATCH = 50

#: Seconds between requests, so one import cannot burst past NCBI's limit -- 3/second without
#: an API key, 10 with one. The same value and the same reasoning as
#: `mutint_sample.management.commands.ncbi_accessions.POLITE_DELAY_SECONDS`; nothing here is in
#: a hurry beside the download itself.
#:
#: **It bounds one import, not a deployment.** The download deliberately runs outside
#: `import_lock` -- see `upload_session.finalize_upload` -- so two people importing at the same
#: moment can together exceed the keyless limit. Establishing a reference happens once per
#: experiment, and the consequence is an HTTP 429 answered as a sentence, so this is not worth
#: a cross-process limiter.
POLITE_DELAY_SECONDS = 0.4

#: The staging subdirectory downloads land in. Its own directory rather than the staging root
#: so a download can never land on a file somebody dropped, and so that re-finalizing -- which
#: the rename confirmation does -- can rebuild it wholesale rather than accumulate copies.
STAGED_SUBDIR = "ncbi"

#: What a downloaded file is called. `.gbk` is what `reference.detect_format` recognises,
#: though it sniffs the content as well.
SUFFIX = ".gbk"

#: Characters a staged filename may take from an accession. `accessions.parse` has already
#: refused anything else, so this is belt and braces around a value that becomes a path.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

_RATE_LIMITED = (
    "NCBI is rate-limiting this deployment. Wait a moment and try again -- or set "
    "MUTINT_NCBI_API_KEY, which raises the limit.")


class FetchError(Exception):
    """A fetch could not be completed, for a reason worth putting in front of a person.

    One type for every way this fails -- no such record, no network, a record with no
    sequence -- because every caller does the same thing with it: shows the sentence. What
    distinguishes the cases is the sentence, and it always names the accession at fault.
    """


class Plan:
    """One accession somebody typed, and what NCBI says it is.

    `records` is `[{"accession", "length"}, ...]`: the token's own versioned accession for a
    nucleotide record, and the assembly's member sequences for a genome. `filename` is what the
    download will be called, settled here so the create-time refusal can talk about it before
    anything is fetched.
    """

    def __init__(self, typed, database, records, title=""):
        self.typed = typed
        self.kind = database
        self.records = [dict(record) for record in records]
        self.title = title
        self.filename = _SAFE_NAME.sub("_", typed) + SUFFIX

    def __repr__(self):
        return "Plan(%r, %r, %d records)" % (self.typed, self.kind, len(self.records))

    @property
    def total_length(self):
        return sum(record.get("length") or 0 for record in self.records)

    @property
    def accessions(self):
        return [record["accession"] for record in self.records]

    @property
    def staged_path(self):
        """Where this plan's download sits, relative to the staging root."""
        return os.path.join(STAGED_SUBDIR, self.filename)

    def as_dict(self):
        """The JSON-safe form stored on the upload session between create and finalize."""
        return {"typed": self.typed, "kind": self.kind, "title": self.title,
                "filename": self.filename, "records": [dict(r) for r in self.records]}

    @classmethod
    def from_dict(cls, payload):
        plan = cls(payload.get("typed") or "", payload.get("kind") or "",
                   payload.get("records") or [], payload.get("title") or "")
        # Honour the stored filename rather than recomputing it: the create-time refusal
        # promised the client that this exact name was free.
        if payload.get("filename"):
            plan.filename = payload["filename"]
        return plan


# --- resolving ------------------------------------------------------------------------------

def resolve(tokens):
    """What each accession in `tokens` is, without downloading a genome.

    Raises `FetchError` naming the first token that cannot be used. This runs before a byte is
    uploaded -- see `upload_session.create_upload_session` -- so a typo costs the round trip
    that caught it and nothing else: no session row, no staging directory, no upload to do
    again. It is the reason mutint-breseq preflights its command line before claiming the
    staged reads, and a better place for it, because here it is genuinely before the upload
    rather than before the claim.
    """
    plans = []
    for index, token in enumerate(tokens):
        if index:
            time.sleep(POLITE_DELAY_SECONDS)
        if kind(token) == ASSEMBLY:
            plans.append(_resolve_assembly(token))
        else:
            plans.append(_resolve_nucleotide(token))

    total = sum(plan.total_length for plan in plans)
    if total > max_bases():
        # On the total rather than per record, deliberately: the drop is *one* reference, and
        # a ceiling applied per contig says nothing at all about a 900-scaffold assembly.
        raise FetchError(
            "That is %s bases, past the %s this deployment will download in one import."
            % (format(total, ",d"), format(max_bases(), ",d")))
    return plans


def _resolve_nucleotide(token):
    try:
        found = summary(token)
    except Unreachable as error:
        raise FetchError("Could not reach NCBI to look up %s: %s" % (token, error))
    if found is None:
        raise FetchError("%s is not a record in %s." % (token, database_name(token)))

    versioned, length = found
    if not length:
        # A WGS master record: a real accession with no sequence of its own. Downloading it
        # would establish a reference of no bases, so it is refused where the answer is
        # useful -- the assembly accession is what that person actually wants.
        raise FetchError(
            "%s carries no sequence of its own -- it looks like a WGS master record. Use the "
            "genome assembly accession (GCA_ or GCF_) instead." % versioned)
    return Plan(token, kind(token), [{"accession": versioned, "length": length}])


def _resolve_assembly(token):
    """An assembly accession, as the nucleotide accessions it is made of.

    **Every sequence it lists**, not only its chromosomes: a plasmid or an unplaced scaffold
    left out is sequence that mutations could have been called against, and leaving it out
    silently is a missing reference that only surfaces as a sample refused months later. The
    count ceiling is what keeps that bounded.

    `GCF_` takes each sequence's RefSeq accession and `GCA_` its GenBank one -- the family the
    person asked for, so the accession recorded against a contig is the one they would find if
    they went looking. Either falls back to the other, because an assembly may list a sequence
    under only one of them.
    """
    reports = _sequence_reports(token)
    if not reports:
        raise FetchError("%s is not a record in %s." % (token, database_name(token)))
    if len(reports) > MAX_SEQUENCES:
        raise FetchError(
            "%s is %d sequences, more than one import will download (%d)."
            % (token, len(reports), MAX_SEQUENCES))

    prefer_refseq = token.upper().startswith("GCF_")
    records = []
    for report in reports:
        first = (report.get("refseq_accession") if prefer_refseq
                 else report.get("genbank_accession"))
        second = (report.get("genbank_accession") if prefer_refseq
                  else report.get("refseq_accession"))
        accession = (first or second or "").strip()
        if not accession:
            # A sequence NCBI lists under neither accession cannot be fetched, and skipping it
            # quietly is the very thing the docstring above refuses to do.
            raise FetchError(
                "%s lists a sequence (%s) with no nucleotide accession, so it cannot be "
                "downloaded." % (token, report.get("sequence_name") or "unnamed"))
        try:
            length = int(report.get("length") or 0)
        except (TypeError, ValueError):
            length = 0
        records.append({"accession": accession, "length": length})

    return Plan(token, ASSEMBLY, records, title=token)


def _sequence_reports(token):
    """Every sequence report for an assembly, following Datasets' pages.

    **Pagination is not optional here.** A complete genome answers in one page and a draft
    assembly of several hundred scaffolds does not, so stopping at the first page would import
    a *partial* genome -- the worst failure available, because it looks like success and is
    only discovered when a sample is refused against a reference missing half its contigs.
    `total_count` is cross-checked against what was collected for the same reason: a page loop
    that ends early has to fail rather than return what it has.
    """
    url = DATASETS_SEQUENCE_REPORTS % _quote(token)
    reports = []
    total_count = None
    page_token = None
    while True:
        params = {"page_size": DATASETS_PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        payload = _datasets_json(url, params, token)
        page = payload.get("reports") or []
        reports.extend(page)
        if total_count is None:
            try:
                total_count = int(payload.get("total_count"))
            except (TypeError, ValueError):
                total_count = None
        page_token = payload.get("next_page_token")
        if not page_token or not page:
            break
        if len(reports) > MAX_SEQUENCES:
            # Stop the moment the answer is too big to import: there is no point walking a
            # 5,000-contig assembly to the end in order to refuse it.
            break
        time.sleep(POLITE_DELAY_SECONDS)

    if (total_count is not None and reports and len(reports) < total_count
            and len(reports) <= MAX_SEQUENCES):
        raise FetchError(
            "NCBI listed %d of %s's %d sequences and then stopped. Nothing was imported, "
            "because a reference missing contigs is worse than none."
            % (len(reports), token, total_count))
    return reports


def _datasets_json(url, params, token):
    """A Datasets GET as parsed JSON, or `FetchError`.

    An unknown assembly answers `200` with an empty object rather than a 404, so *no such
    record* is the caller's reading of an empty `reports` list rather than a status code.

    **The API key goes in a header here and in a query parameter for eutils**, because that is
    what each service takes. `request_params()` is therefore not used for this call; of what it
    carries, only `tool` would have meant anything to Datasets anyway.
    """
    try:
        response = requests.get(url, params=params, headers=_datasets_headers(),
                                timeout=timeout_seconds())
    except requests.RequestException as error:
        raise FetchError("Could not reach NCBI to look up %s: %s" % (token, redact(error)))
    if response.status_code == 404:
        raise FetchError("%s is not a record in %s." % (token, database_name(token)))
    if response.status_code == 429:
        raise FetchError(_RATE_LIMITED)
    if response.status_code != 200:
        raise FetchError("NCBI answered HTTP %s for %s." % (response.status_code, token))
    try:
        return response.json() or {}
    except ValueError as error:
        raise FetchError("NCBI's answer about %s was not JSON (%s)." % (token, redact(error)))


def _datasets_headers():
    api_key = getattr(settings, "MUTINT_NCBI_API_KEY", "")
    return {"api-key": api_key} if api_key else {}


def _quote(token):
    from urllib.parse import quote

    return quote(token, safe="")


# --- downloading ----------------------------------------------------------------------------

def download(staged_root, plans, report=None):
    """Download `plans` into `staged_root` and say which record each contig came from.

    Returns `{sha256: {"accession", "length", "detail"}}` over every record written -- an
    assembly contributes one entry per sequence. That map is what
    `mutint_sample.ncbi.record_downloaded` turns into the NCBI link, and it is keyed on the
    digest because that is what a `DatabaseSequenceLink` is keyed on.

    `plans` are `Plan`s or the dicts stored on the session; `report` is an optional
    `callable(message)` for the page's progress line.

    **The accessions are read back off the files, not carried from the request.** That is what
    makes recording them as verified honest: what is recorded is what the downloaded record
    says it is, so a fetch that came back as something other than what was asked for cannot be
    filed under what was asked for. The same read is the integrity check -- a requested record
    that never arrived is a failure naming it, which is what catches an efetch that answered an
    error page.
    """
    plans = [plan if isinstance(plan, Plan) else Plan.from_dict(plan) for plan in plans]

    # Rebuilt wholesale rather than added to. A declined-then-confirmed rename finalizes the
    # same session twice, and a second download landing beside the first would import the
    # genome twice over.
    root = os.path.join(staged_root, STAGED_SUBDIR)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)

    downloaded = {}
    for index, plan in enumerate(plans):
        if report is not None:
            report("Downloading %s from NCBI…" % plan.typed)
        if index:
            time.sleep(POLITE_DELAY_SECONDS)
        path = os.path.join(root, plan.filename)
        _write(plan, path)
        arrived = []
        for accession, digest, length in _records_in(path, plan.typed):
            arrived.append(accession)
            # **Arrival is counted separately from being keyed by digest**, because two
            # records can carry the same bases -- two accessions for one sequence, or an
            # assembly listing an identical plasmid twice. Those collapse to one entry here,
            # since a `DatabaseSequenceLink` is unique on the digest and only one of them can
            # hold the link; reading arrival off this map would then report the collapsed
            # ones as records NCBI never sent.
            downloaded.setdefault(digest, {
                "accession": accession,
                "length": length,
                "detail": _detail(plan, accession, length),
            })
        _check_every_record_arrived(plan, arrived)
    return downloaded


def _detail(plan, accession, length):
    """The sentence `/mutations/reference` shows beside the confirmed contig."""
    if plan.kind == ASSEMBLY and plan.typed.upper() != accession.upper():
        return ("Downloaded from NCBI as %s, part of assembly %s, when this reference was "
                "imported (%s bases)." % (accession, plan.typed, format(length, ",d")))
    return ("Downloaded from NCBI as %s when this reference was imported (%s bases)."
            % (accession, format(length, ",d")))


def _check_every_record_arrived(plan, arrived):
    seen = {accession.upper() for accession in arrived}
    missing = [accession for accession in plan.accessions
               if accession.upper() not in seen]
    if missing:
        raise FetchError(
            "NCBI did not return %s for %s. Nothing was imported, because a reference "
            "missing contigs is worse than none."
            % (", ".join(missing[:5]) + ("…" if len(missing) > 5 else ""), plan.typed))


def _write(plan, path):
    """Write every record of `plan` to `path`, one efetch per batch of accessions."""
    accessions = plan.accessions
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for start in range(0, len(accessions), FETCH_BATCH):
            if start:
                time.sleep(POLITE_DELAY_SECONDS)
            batch = accessions[start:start + FETCH_BATCH]
            for chunk in _efetch(batch, plan.typed):
                handle.write(chunk)


def _efetch(batch, token):
    """Stream the GenBank text for `batch`, or raise `FetchError`."""
    try:
        response = requests.get(
            EFETCH_URL,
            # gbwithparts, never gb -- see the module docstring. A CON record fetched as `gb`
            # arrives with no sequence at all and imports as a reference of length zero.
            params=request_params(db="nuccore", id=",".join(batch),
                                  rettype="gbwithparts", retmode="text"),
            timeout=timeout_seconds(),
            stream=True)
    except requests.RequestException as error:
        raise FetchError("Could not reach NCBI to download %s: %s" % (token, redact(error)))

    if response.status_code == 429:
        response.close()
        raise FetchError(_RATE_LIMITED)
    if response.status_code != 200:
        response.close()
        raise FetchError(
            "NCBI answered HTTP %s downloading %s." % (response.status_code, token))

    try:
        for chunk in response.iter_content(chunk_size=1 << 16, decode_unicode=True):
            if not chunk:
                continue
            yield chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
    except requests.RequestException as error:
        raise FetchError("The download of %s stopped partway (%s)." % (token, redact(error)))
    finally:
        response.close()


def _records_in(path, token):
    """`[(accession, sha256, length), ...]` for every record in a downloaded GenBank.

    The accession is the record's **VERSION**, the versioned form the Sequence Viewer is
    addressed by -- deliberately not the LOCUS name the contig is imported under. The two
    differ (`NC_000913` and `NC_000913.3`), and the link wants the one that names these exact
    bases. `seq_ids[].sha256` is what ties them together, being keyed on the bases rather than
    on either name.

    The digest is `reference.sequence_digest`, the very function that computes what is stored
    in `seq_ids`, so a downloaded contig and its stored entry cannot disagree about their key.
    """
    from mutint_import.annotate.genbank import parse_records

    from Bio.Seq import UndefinedSequenceError

    records = []
    for record in parse_records(path):
        accession = (record.id or "").strip()
        try:
            sequence = str(record.seq or "")
        except UndefinedSequenceError:
            # How Biopython actually reports a record whose bases are not in the file: the
            # attribute exists and raises when read, rather than being empty.
            sequence = ""
        if not sequence:
            # The gbwithparts trap: a CON record fetched as plain `gb` parses cleanly into a
            # record with no bases. Refused here rather than established as a reference with
            # the right contig names and nothing in them.
            raise FetchError(
                "NCBI returned %s (in %s) with no sequence."
                % (accession or "a record", token))
        if not accession or accession.startswith("<unknown"):
            # Nothing to file a link under. The sequence is perfectly importable, so this is a
            # link not recorded rather than a download that failed.
            logger.info("a record in %s carries no VERSION; not linking it", token)
            continue
        records.append((accession, reference_io.sequence_digest(sequence), len(sequence)))
    if not records:
        raise FetchError("NCBI returned nothing readable for %s." % (token,))
    return records

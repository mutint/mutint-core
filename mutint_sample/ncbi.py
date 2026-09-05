"""Confirming that a reference contig really is the NCBI record somebody said it was.

The NCBI Sequence Viewer draws a locus from NCBI's own annotation, addressed by accession.
Nothing in this repo stores an accession -- see `DatabaseSequenceLink`'s docstring for why -- so one
has to be supplied by a person, and the whole value of this module is that a supplied name is
*not* taken at its word.

**This is a verification, not a search.** It never asks NCBI which record holds a sequence; it
asks whether the record already named is made of these bases, and answers yes or no. Two
consequences worth knowing before extending it:

* It cannot discover an accession. A reference called `REL606` or `chromosome` stays unusable
  until somebody states what it is. That is the accepted cost of never guessing.
* The sequence never leaves the deployment. We send an accession and receive a genome, then
  compare locally. A BLAST-style lookup would be the other way round, which for an
  unpublished ALE reference is a different proposition entirely.

The comparison is exact: sha256 of the uppercased bases, against the digest
`mutint_import.reference.sequence_entries` already stored per contig. No local file is opened
and no alignment is performed -- the record either is our sequence or it is not.

**This module is one database's implementation, and the model is not.**
`DatabaseSequenceLink` carries a `database` column because the *idea* -- a verdict keyed on
the bases -- is not NCBI's; everything below is eutils and nothing else. So the lookups here
take a `database` and default it to NCBI's nucleotide database, while `verify` does not take
one at all: it could not honour it. A second database is a sibling of this file, not a branch
inside it.
"""

import hashlib
import logging

import requests
from django.conf import settings
from django.utils import timezone

from mutint_sample.models import DatabaseSequenceLink, ReferenceSequences

logger = logging.getLogger(__name__)

ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

#: Sent on every request. NCBI asks callers to identify their tool, and an unidentified
#: caller is the one most likely to be throttled.
TOOL_NAME = "mutint"


class _Unreachable(Exception):
    """NCBI could not be asked. Distinct from any answer it might have given."""


def sequence_digest_stream(lines):
    """sha256 of the bases in FASTA `lines`, one line at a time.

    The streaming twin of `mutint_import.reference.sequence_digest`, so a genome is never held
    in memory whole. It must agree with that function exactly -- two digest implementations
    that can disagree would make every verdict here meaningless while still looking like it
    worked -- so `test_ncbi.py` hashes the same FASTA both ways and asserts equality rather
    than trusting the two to stay in step.

    Header lines are skipped, so a multi-record stream digests as the concatenation of its
    bases. Callers that care about record boundaries must split first; `verify` fetches one
    accession at a time and does not.
    """
    digest = hashlib.sha256()
    total = 0
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.strip()
        if not line or line.startswith(">"):
            continue
        bases = line.upper()
        digest.update(bases.encode("utf-8"))
        total += len(bases)
    return digest.hexdigest(), total


def _params(**extra):
    params = {"tool": TOOL_NAME}
    email = getattr(settings, "MUTINT_NCBI_EMAIL", "")
    api_key = getattr(settings, "MUTINT_NCBI_API_KEY", "")
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    params.update(extra)
    return params


def _timeout():
    return getattr(settings, "MUTINT_NCBI_TIMEOUT", 30)


def _max_bases():
    return getattr(settings, "MUTINT_NCBI_MAX_BASES", 50_000_000)


def _summary(accession):
    """`(accessionversion, slen)` for `accession`, or None when NCBI has no such record.

    Raises `_Unreachable` when NCBI could not be asked at all, which is deliberately not the
    same outcome as being told there is no such record.
    """
    try:
        response = requests.get(
            ESUMMARY_URL,
            params=_params(db="nuccore", id=accession, retmode="json"),
            timeout=_timeout())
    except requests.RequestException as error:
        raise _Unreachable(str(error))

    if response.status_code != 200:
        raise _Unreachable("NCBI answered HTTP %s" % response.status_code)

    try:
        payload = response.json()
    except ValueError as error:
        raise _Unreachable("NCBI's answer was not JSON (%s)" % error)

    result = (payload or {}).get("result") or {}
    uids = result.get("uids") or []
    if not uids:
        return None

    record = result.get(uids[0]) or {}
    # An unresolvable id can still come back as a record carrying an error string.
    if record.get("error"):
        return None

    versioned = record.get("accessionversion") or accession
    try:
        length = int(record.get("slen"))
    except (TypeError, ValueError):
        raise _Unreachable("NCBI reported no length for %s" % versioned)
    return versioned, length


def _fetch_digest(accession, expected_length):
    """`(sha256, base_count)` for NCBI's FASTA of `accession`, streamed rather than buffered."""
    if expected_length > _max_bases():
        raise _Unreachable(
            "%s is %s bases, past the %s-base ceiling this check will download"
            % (accession, format(expected_length, ",d"), format(_max_bases(), ",d")))
    try:
        response = requests.get(
            EFETCH_URL,
            params=_params(db="nuccore", id=accession, rettype="fasta", retmode="text"),
            timeout=_timeout(),
            stream=True)
    except requests.RequestException as error:
        raise _Unreachable(str(error))

    if response.status_code != 200:
        raise _Unreachable("NCBI answered HTTP %s for the sequence" % response.status_code)

    try:
        return sequence_digest_stream(response.iter_lines())
    except requests.RequestException as error:
        raise _Unreachable("the download stopped partway (%s)" % error)
    finally:
        response.close()


def verify(sha256, length, accession):
    """Check `accession` against a contig, returning `(status, accession, detail)`.

    Two stages, cheapest first. The summary settles the length for one small request, and a
    length that differs is a definitive no -- so most wrong answers cost no download at all.
    Only a candidate that survives that is worth fetching a genome for.

    The returned accession is NCBI's own versioned form on success, not what was passed in:
    the point of the exercise is to end up holding an identifier that names exactly the
    sequence that matched.

    Never raises. Every way of failing to reach NCBI is ERROR with a sentence, because this
    is called from a request and from a management command, and neither wants a traceback
    for a network that was busy.
    """
    accession = (accession or "").strip()
    if not accession:
        return DatabaseSequenceLink.UNCHECKED, "", ""

    try:
        summary = _summary(accession)
    except _Unreachable as error:
        return DatabaseSequenceLink.ERROR, accession, "Could not reach NCBI: %s" % error

    if summary is None:
        return (DatabaseSequenceLink.NOT_FOUND, accession,
                "NCBI has no nucleotide record under %s." % accession)

    versioned, ncbi_length = summary
    if ncbi_length != length:
        return (DatabaseSequenceLink.MISMATCH, versioned,
                "%s is %s bases; this contig is %s. They are different sequences."
                % (versioned, format(ncbi_length, ",d"), format(length, ",d")))

    try:
        digest, counted = _fetch_digest(versioned, ncbi_length)
    except _Unreachable as error:
        return DatabaseSequenceLink.ERROR, versioned, "Could not reach NCBI: %s" % error

    if counted != length:
        # The summary and the FASTA disagreed, so we cannot say what we compared.
        return (DatabaseSequenceLink.ERROR, versioned,
                "NCBI reported %s bases for %s but sent %s."
                % (format(ncbi_length, ",d"), versioned, format(counted, ",d")))

    if digest != sha256:
        return (DatabaseSequenceLink.MISMATCH, versioned,
                "%s is the same length as this contig but not the same sequence -- most "
                "likely a different strain or assembly." % versioned)

    return (DatabaseSequenceLink.VERIFIED, versioned,
            "%s matches this contig exactly (%s bases)." % (versioned, format(length, ",d")))


def record_for(sha256, database=DatabaseSequenceLink.NCBI_NUCLEOTIDE):
    """The stored verdict for a contig digest, or None when nobody has checked it."""
    if not sha256:
        return None
    return DatabaseSequenceLink.objects.filter(database=database, sha256=sha256).first()


def records_for(digests, database=DatabaseSequenceLink.NCBI_NUCLEOTIDE):
    """`{sha256: DatabaseSequenceLink}` for many contigs at once.

    One query, because the mutation table asks per row and an experiment's table can run to
    hundreds of them.

    **Keyed by digest alone, which is only unambiguous because the query is scoped.** A digest
    may hold a row per database; `database` is what makes at most one of them reachable here,
    so a caller cannot be handed some other database's verdict under a key that does not say
    so.
    """
    wanted = [digest for digest in digests if digest]
    if not wanted:
        return {}
    return {record.sha256: record
            for record in DatabaseSequenceLink.objects.filter(
                database=database, sha256__in=wanted)}


def check_and_store(sha256, length, accession, user=None,
                    database=DatabaseSequenceLink.NCBI_NUCLEOTIDE):
    """Run `verify` and persist the verdict, returning the `DatabaseSequenceLink` row.

    The row is written whatever the outcome, including the failures: a MISMATCH somebody has
    already paid for should not be re-fetched by the next reader, and an ERROR is worth
    keeping so `--list` can show what went wrong without anybody watching a page at the time.

    `database` reaches the row's key and *not* `verify`, which speaks eutils and could not
    honour another one. Passing something else here would store an NCBI verdict under another
    database's name -- so the parameter exists for the day this file has a sibling, and until
    then only the default is correct.
    """
    status, resolved, detail = verify(sha256, length, accession)
    record, _created = DatabaseSequenceLink.objects.update_or_create(
        database=database,
        sha256=sha256,
        defaults={
            "length": length,
            "accession": resolved,
            "status": status,
            "detail": detail,
            "checked_at": timezone.now(),
            "proposed_by": user if (user is not None and user.is_authenticated) else None,
        })
    if status != DatabaseSequenceLink.VERIFIED:
        logger.info("NCBI check for %s (%s): %s -- %s", accession, sha256[:12], status, detail)
    return record


def verified_contig_names(experiment, database=DatabaseSequenceLink.NCBI_NUCLEOTIDE):
    """The contig names of `experiment` confirmed to be a record in `database`, as a set.

    Empty when the experiment has no reference, when nothing has been checked, or when a
    check failed -- all of which mean the same thing to a caller: do not offer a link to
    NCBI's coordinate space for this contig.

    Two queries at most, whatever the size of the table.
    """
    if experiment is None:
        return frozenset()
    try:
        reference = experiment.reference
    except (ReferenceSequences.DoesNotExist, AttributeError):
        return frozenset()

    by_digest = {entry["sha256"]: entry["id"]
                 for entry in (reference.seq_ids or [])
                 if entry.get("sha256") and entry.get("id")}
    if not by_digest:
        return frozenset()

    return frozenset(by_digest[digest]
                     for digest, record in records_for(by_digest, database).items()
                     if record.is_verified)


def contig_entry(experiment, seq_id):
    """The `seq_ids` entry for one contig as `{id, length, sha256, aliases}`, or None.

    The digest is the whole reason this lookup exists -- it is what a verdict is keyed on --
    and it must not be rendered: `browse._reference_urls` strips it from the config it hands
    the page, calling it identity material, and every caller here has the same duty.

    A reference established before per-sequence hashing has no digest, so there is nothing to
    verify against and this answers None rather than offering a check that could not conclude.
    """
    if experiment is None or not seq_id:
        return None
    try:
        reference = experiment.reference
    except (ReferenceSequences.DoesNotExist, AttributeError):
        return None

    for entry in reference.seq_ids or []:
        if entry.get("id") != seq_id:
            continue
        if not entry.get("sha256") or not entry.get("length"):
            return None
        return {"id": entry["id"], "length": entry["length"],
                "sha256": entry["sha256"], "aliases": list(entry.get("aliases") or [])}
    return None


def contig_states(experiment, database=DatabaseSequenceLink.NCBI_NUCLEOTIDE):
    """Every contig of `experiment`'s reference with its verdict, for the Reference page.

    One query for the verdicts however many contigs there are. `sha256` is deliberately not
    in what comes back: this feeds a template, and the digest is identity material.

    `checkable` says whether an accession could be recorded for this contig at all --
    false for one whose reference predates per-sequence hashing, where there is nothing to
    compare a record against.
    """
    if experiment is None:
        return []
    try:
        reference = experiment.reference
    except (ReferenceSequences.DoesNotExist, AttributeError):
        return []

    entries = list(reference.seq_ids or [])
    verdicts = records_for([entry.get("sha256") for entry in entries], database)

    states = []
    for entry in entries:
        record = verdicts.get(entry.get("sha256"))
        states.append({
            "id": entry.get("id") or "",
            "length": entry.get("length") or 0,
            # Formatted here rather than in the template because Django has no comma filter
            # without contrib.humanize, and `mutation_table_builder` already formats its
            # Position column this way. A genome length is unreadable without it.
            "length_display": format(entry.get("length") or 0, ",d"),
            "aliases": list(entry.get("aliases") or []),
            "checkable": bool(entry.get("sha256") and entry.get("length")),
            "record": record,
            "status": record.status if record else DatabaseSequenceLink.UNCHECKED,
            "accession": record.accession if record else "",
            "detail": record.detail if record else "",
            "is_verified": bool(record and record.is_verified),
        })
    return states

"""What somebody typed into the NCBI accessions box, turned into a list of accessions.

Pure: no network, no Django, no models. The rules most likely to be got wrong here are what
counts as a separator and which database a token belongs to, and both are testable without
asking NCBI anything. `mutint_import.ncbi_fetch` is what does the asking.

**Characters are checked; shape is not.** There is no pattern here deciding what an accession
looks like, and that is deliberate -- see **There is no accession stored anywhere** in
mutint-core's CLAUDE.md: a pattern strict enough to reject a local name like `REL606` would
also reject accession formats it was not written for, and *that* failure is invisible, because
the token simply never becomes eligible. So a token is refused only for holding characters no
accession can hold, and everything else is NCBI's question to answer with *no such record*.

The one exception is `kind()`, which routes a token to one of the two databases. It is safe
where a shape check is not: `GCA_`/`GCF_` is NCBI's own assembly namespace and no nucleotide
accession lives in it, and a token routed to the wrong database fails by being *not found
there*, which is a sentence naming the database rather than a silent ineligibility.
"""

import re

#: How many accessions one import may name. Well past what anybody types by hand, and low
#: enough that a pasted spreadsheet column is refused rather than turned into 4,000 requests.
MAX_ACCESSIONS = 25

#: How many sequences one accession may bring. An assembly accession is one token that can
#: mean hundreds of contigs -- see `ncbi_fetch.resolve`, which is what enforces this.
MAX_SEQUENCES = 500

#: Long enough for anything NCBI issues; short enough that a pasted sentence is not a token.
MAX_TOKEN_LENGTH = 40

#: Commas, semicolons and any whitespace, which is "commas, spaces or new lines" as the page
#: puts it. Tabs and CRLF come free, which matters because a pasted column arrives as either.
_SEPARATORS = re.compile(r"[,;\s]+")

#: The characters an accession can be made of. Not a shape -- see the module docstring.
_ALLOWED = re.compile(r"^[A-Za-z0-9._-]+$")

#: NCBI's assembly namespace, and the whole of how the two databases are told apart.
_ASSEMBLY = re.compile(r"^GC[AF]_", re.IGNORECASE)

NUCLEOTIDE = "nucleotide"
ASSEMBLY = "assembly"

#: What each kind is called in a sentence a person reads.
DATABASE_NAMES = {
    NUCLEOTIDE: "NCBI's nucleotide database",
    ASSEMBLY: "NCBI's genome database",
}


class AccessionError(ValueError):
    """What was typed cannot be asked about, for a reason worth saying in one sentence.

    A ValueError because every caller here treats it as bad input; the message is the whole
    payload, and it names the token at fault rather than describing the rule in the abstract.
    """


def parse(text):
    """`text` as a list of accessions: in the order typed, without repeats.

    Empty text is an empty list rather than an error -- "no accessions" is a perfectly good
    thing for the box to say, and it is the caller who knows whether files were dropped too.
    """
    if not text or not text.strip():
        return []

    tokens = []
    for raw in _SEPARATORS.split(text.strip()):
        if not raw:
            continue
        if len(raw) > MAX_TOKEN_LENGTH:
            raise AccessionError(
                "%r… is too long to be an accession." % raw[:MAX_TOKEN_LENGTH])
        if not _ALLOWED.match(raw):
            raise AccessionError(
                "%r is not an accession -- accessions are letters, digits, '.', '_' and '-', "
                "separated by commas, spaces or new lines." % raw)
        # Case-insensitively, because NCBI is: `nc_000913.3` and `NC_000913.3` are one
        # accession and one download, and de-duplicating on the raw text would fetch both.
        if any(existing.lower() == raw.lower() for existing in tokens):
            continue
        tokens.append(raw)

    if len(tokens) > MAX_ACCESSIONS:
        raise AccessionError(
            "%d accessions is more than one import can take (%d at a time)."
            % (len(tokens), MAX_ACCESSIONS))
    return tokens


def kind(token):
    """Which database `token` names a record in: ASSEMBLY or NUCLEOTIDE."""
    return ASSEMBLY if _ASSEMBLY.match(token or "") else NUCLEOTIDE


def database_name(token):
    """The database `token` will be looked for in, named the way a sentence needs it."""
    return DATABASE_NAMES[kind(token)]

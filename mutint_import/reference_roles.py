"""What each contig of a reference is *for*, as breseq's three reference options.

breseq takes reference sequences under three flags, and which one a sequence arrives under
changes how it is analysed:

===================  ====  ==========================================================
role                 flag  breseq's rule
===================  ====  ==========================================================
``reference``        -r    one coverage distribution fitted per sequence
``contig``           -c    **one distribution fitted across every sequence in the
                           file** -- what a draft assembly's contigs want, since they
                           are one chromosome at one copy number, and what keeps a
                           short contig from being fitted on its own
``junction_only``    -s    used only for calling junctions against the others; no
                           coverage is fitted and no mutations are called on it. A
                           transposon that is not in the reference genome.
===================  ====  ==========================================================

**breseq expresses this by which input file a sequence is in**, and MutInt has no input
files: `reference.normalize_references` merges every uploaded file into one canonical GFF3
and FASTA before anything is stored, and the originating filename is used for duplicate
warnings and then dropped. So the grouping has to be recorded per contig and the files
rebuilt from it, which `rendered_groups` does through `reference_export`.

**A role changes nothing about the data.** Not the bases, not the annotation, not a
mutation's gene names, and nothing derived -- no rebuild is requested and no reannotation
runs, which is what makes this much cheaper than `annotation.install_annotation`, the seam
it otherwise resembles. What it changes is the command line of every *future* breseq run.

Storage is a ``role`` key on the contig's `ReferenceSequences.seq_ids` entry, and **an
absent key means the guess** -- so only an answer somebody actually gave is persisted,
nothing needs backfilling, a new experiment needs no seeding, and no migration was
required to add any of this. It is the posture a missing `DerivedDataState` row takes.
`reference_store._apply_sequence_fields` carries the key across a rewrite and
`reference_rename._record_aliases` across a rename, both beside `aliases`, which is the
one invariant here that is maintained by hand.
"""

import re

ROLE_REFERENCE = "reference"
ROLE_CONTIG = "contig"
ROLE_JUNCTION_ONLY = "junction_only"

#: Every role, in the order they are offered and rendered.
ROLES = (ROLE_REFERENCE, ROLE_CONTIG, ROLE_JUNCTION_ONLY)

DEFAULT_ROLE = ROLE_REFERENCE

ROLE_LABELS = {
    ROLE_REFERENCE: "Reference (-r)",
    ROLE_CONTIG: "Contig (-c)",
    ROLE_JUNCTION_ONLY: "Junction-only (-s)",
}

ROLE_FLAGS = {
    ROLE_REFERENCE: "-r",
    ROLE_CONTIG: "-c",
    ROLE_JUNCTION_ONLY: "-s",
}

ROLE_DESCRIPTIONS = {
    ROLE_REFERENCE: "Coverage is fitted to this sequence on its own.",
    ROLE_CONTIG: "One coverage distribution is fitted across every sequence marked this "
                 "way, which is what contigs of one draft assembly want.",
    ROLE_JUNCTION_ONLY: "Used only for calling junctions against the other sequences. No "
                        "coverage is fitted and no mutations are called on it.",
}

#: Names an assembler gives a contig. Anchored, and digits are required after the word, so
#: a finished genome called `Contigo` or a plasmid called `NODEL` is not swept up. Kept
#: deliberately narrow: widening it later is cheap, and narrowing it silently changes what
#: every experiment that never set a role explicitly does.
_ASSEMBLER_NAME = re.compile(
    r"^(?:node|contig|ctg|scaffold|scf)[._-]?\d"   # SPAdes, SKESA, SOAP, common spellings
    r"|^k\d+[._-]\d",                              # megahit: k141_12345
    re.IGNORECASE,
)


def guess_role(seq_id):
    """The role a contig's *name* suggests, with no database and no reference loaded.

    `ROLE_CONTIG` for something an assembler named, `ROLE_REFERENCE` otherwise.

    **`ROLE_JUNCTION_ONLY` is never guessed.** There is no name that reliably means "this
    sequence is not in the genome", and the cost of the two mistakes is not symmetric: a
    contig wrongly left as `-r` is fitted its own coverage, while a sequence wrongly made
    junction-only has every mutation on it silently uncalled.

    A guess is shown as a suggestion on the Reference page and is overridden by writing an
    explicit role, so being wrong here is visible and costs one click -- which is the whole
    reason guessing from a name is defensible at all.
    """
    return ROLE_CONTIG if _ASSEMBLER_NAME.match(seq_id or "") else ROLE_REFERENCE


def normalize_role(role):
    """`role` if it is one of `ROLES`, else None. Callers turn None into their own refusal."""
    return role if role in ROLES else None


def entry_role(entry):
    """The effective role of one `seq_ids` entry: its explicit role, or the guess."""
    explicit = normalize_role((entry or {}).get("role"))
    return explicit or guess_role((entry or {}).get("id"))


def entry_is_guessed(entry):
    """Whether this entry's role is the suggestion rather than an answer somebody gave."""
    return normalize_role((entry or {}).get("role")) is None


def _entries(experiment):
    """The experiment's `seq_ids`, or an empty list when it has no reference."""
    reference = getattr(experiment, "reference", None)
    return list(getattr(reference, "seq_ids", None) or [])


def roles_for(experiment):
    """`{seq_id: role}` for every contig, guesses filled in. Empty without a reference."""
    return {entry["id"]: entry_role(entry) for entry in _entries(experiment)}


def states_for(experiment):
    """`{seq_id: {"role", "label", "flag", "guessed"}}`, what a page renders per contig."""
    states = {}
    for entry in _entries(experiment):
        role = entry_role(entry)
        states[entry["id"]] = {
            "role": role,
            "label": ROLE_LABELS[role],
            "flag": ROLE_FLAGS[role],
            "guessed": entry_is_guessed(entry),
        }
    return states


def grouped(experiment):
    """`[(role, [seq_id, ...]), ...]` in `ROLES` order, omitting roles nothing holds.

    Order is fixed rather than following `seq_ids` so a command line is reproducible, and
    within a role the contigs keep their stored order, which is the FASTA's.
    """
    roles = roles_for(experiment)
    groups = []
    for role in ROLES:
        members = [entry["id"] for entry in _entries(experiment) if roles[entry["id"]] == role]
        if members:
            groups.append((role, members))
    return groups


def is_uniform(experiment):
    """Whether every contig is `ROLE_REFERENCE` -- the case needing no rendering at all.

    True for every experiment that has never been given a role and whose contigs are not
    assembler-named, which is nearly all of them. A caller that honours this passes the
    stored GFF3 straight to `-r` and behaves exactly as it did before roles existed.
    """
    return all(role == ROLE_REFERENCE for role in roles_for(experiment).values())


def describe(experiment):
    """One human sentence naming the grouping, e.g. `3 contigs (-c), pKD46 (-r)`.

    Empty when there is no reference. Used by a page that is about to act on the grouping
    and has to say what it is going to do -- a breseq run is hours, and a grouping nobody
    can see from the page it governs is the expensive kind of invisible.
    """
    parts = []
    for role, members in grouped(experiment):
        flag = ROLE_FLAGS[role]
        if len(members) == 1:
            parts.append("%s (%s)" % (members[0], flag))
        else:
            parts.append("%d sequences (%s)" % (len(members), flag))
    return ", ".join(parts)


def set_roles(experiment, roles):
    """Record explicit roles. `roles` is `{seq_id: role or None}`; None clears the override.

    Returns the number of contigs whose effective role changed. Writes only `seq_ids`, and
    asks for no rebuild and no reannotation -- see this module's docstring.

    An unknown `seq_id` is ignored rather than raised: the page posts what it rendered, and
    a contig that left the reference between render and submit is not worth a 400. An
    invalid role *is* the caller's problem and is refused by `normalize_role` upstream.
    """
    reference = getattr(experiment, "reference", None)
    entries = list(getattr(reference, "seq_ids", None) or [])
    if not entries:
        return 0

    changed = 0
    for entry in entries:
        if entry["id"] not in roles:
            continue
        before = entry_role(entry)
        wanted = roles[entry["id"]]
        if wanted is None:
            entry.pop("role", None)
        else:
            entry["role"] = wanted
        if entry_role(entry) != before:
            changed += 1

    reference.seq_ids = entries
    reference.save(update_fields=["seq_ids"])
    return changed


def rendered_groups(experiment, references):
    """`[(role, flag, gff3_text), ...]` -- one breseq reference file per non-empty role.

    `references` is the loaded reference (`annotation.reference_sequences_for`). Text is
    returned rather than files written: rendering is core's, and where the files belong is
    the caller's -- for `mutint-breseq` that is inside the run directory its own receiver
    already deletes, so this adds no lifecycle to anything.

    GFF3 is the format because it is what the store holds and what breseq is already handed
    today, so a uniform reference rendered through here is the same bytes as the stored file.
    """
    # Imported here rather than at module scope: `reference_export` pulls in Biopython, and
    # this module is otherwise import-cheap enough for the launcher page to ask it per render.
    from mutint_import import reference_export

    rendered = []
    for role, members in grouped(experiment):
        chosen = reference_export.subset(references, members)
        rendered.append((role, ROLE_FLAGS[role], reference_export.render(chosen, "gff3")))
    return rendered

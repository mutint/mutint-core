"""Whether each contig of a reference is circular or linear.

breseq reads a sequence's topology off a GenBank LOCUS line or a GFF3 ``region`` row's
``Is_circular``, and treats an unflagged sequence as linear. It decides how a mutation
spanning the origin is called and what a junction across position 1 means, so it is a
property of the reference worth recording and worth being able to correct: a bare FASTA
says nothing, and a GenBank written by a tool that left the field blank says nothing
either.

Storage is a ``circular`` key on the contig's `ReferenceSequences.seq_ids` entry, true or
false, written when the file stated it (`reference_store` reads the stored GFF3's region
rows) or when somebody set it on the Reference page. **An absent key means nobody has
said**, and is rendered as *linear, suggested* -- breseq's own reading, marked so a person
can see it was never confirmed. That is the posture `reference_roles` takes with a role.

Unlike a role, topology is in the stored file as well as on the row, and the two must
agree: the GFF3 is what mutint-breseq hands breseq untouched when every contig shares a
role, and what every download and the GenBank export are rendered from. So
`set_topology` writes the entries and then has `reference_store.write_topology` rewrite
the file's region rows to match. No rebuild and no reannotation: nothing derived reads it.
"""

LABELS = {True: "circular", False: "linear"}

#: What an entry with no `circular` key is taken to be, and shown as *suggested*.
DEFAULT_CIRCULAR = False


def entry_circular(entry):
    """The effective topology of one `seq_ids` entry: the stored answer, or the default."""
    value = (entry or {}).get("circular")
    return DEFAULT_CIRCULAR if value is None else bool(value)


def entry_is_guessed(entry):
    """Whether this entry's topology is the default rather than something the file or a
    person said."""
    return (entry or {}).get("circular") is None


def label_for(circular):
    return LABELS[bool(circular)]


def parse(value):
    """`True`, `False` or None (clear) from what a page posts; raises ValueError otherwise."""
    if value is None:
        return None
    if value is True or value is False:
        return value
    text = str(value).strip().lower()
    if text in ("circular", "true", "1"):
        return True
    if text in ("linear", "false", "0"):
        return False
    raise ValueError("Topology must be circular or linear.")


def _entries(experiment):
    reference = getattr(experiment, "reference", None)
    return list(getattr(reference, "seq_ids", None) or [])


def states_for(experiment):
    """`{seq_id: {"circular", "label", "guessed"}}`, what a page renders per contig."""
    states = {}
    for entry in _entries(experiment):
        circular = entry_circular(entry)
        states[entry["id"]] = {
            "circular": circular,
            "label": label_for(circular),
            "guessed": entry_is_guessed(entry),
        }
    return states


def set_topology(experiment, topologies):
    """Record topologies. `topologies` is `{seq_id: True|False|None}`; None clears.

    Returns the number of contigs whose effective topology changed. Writes `seq_ids` and
    then the stored GFF3's region rows (`reference_store.write_topology`), so the file
    breseq reads agrees with the page. An unknown `seq_id` is ignored rather than raised,
    for the reason `reference_roles.set_roles` gives.
    """
    from mutint_import import reference_store

    reference = getattr(experiment, "reference", None)
    entries = list(getattr(reference, "seq_ids", None) or [])
    if not entries:
        return 0

    changed = 0
    for entry in entries:
        if entry["id"] not in topologies:
            continue
        before = entry_circular(entry)
        wanted = topologies[entry["id"]]
        if wanted is None:
            entry.pop("circular", None)
        else:
            entry["circular"] = bool(wanted)
        if entry_circular(entry) != before:
            changed += 1

    reference.seq_ids = entries
    reference.save(update_fields=["seq_ids"])
    reference_store.write_topology(
        experiment, {entry["id"]: entry["circular"] for entry in entries if "circular" in entry})
    return changed

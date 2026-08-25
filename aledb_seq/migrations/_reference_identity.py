"""How a stored reference's identity is recomputed from its FASTA, for 0009's backfill.

Underscore-prefixed so the migration loader skips it, and separated from the migration so
the rule can be unit-tested -- the shape `_backfill_rule.py` established.

The FASTA reader and the two digests are **inlined** rather than imported from
`aledb_import.reference`. A migration describes the world as it was; importing app code
makes it re-interpret history every time that code changes. The duplication is held honest
by a test asserting this module agrees with `aledb_import.reference` on the same input.
"""

import hashlib


def parse_fasta(handle):
    """Yield `(seq_id, sequence)`. The id is the first whitespace token of the header."""
    seq_id, chunks = None, []
    for raw in handle:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if seq_id is not None:
                yield seq_id, "".join(chunks)
            seq_id, chunks = line[1:].split()[0] if line[1:].split() else "", []
        elif seq_id is not None:
            chunks.append(line)
    if seq_id is not None:
        yield seq_id, "".join(chunks)


def sequence_digest(sequence):
    return hashlib.sha256(sequence.upper().encode("utf-8")).hexdigest()


def sequence_set_digest(sequences):
    joined = "".join(sorted(sequence_digest(seq) for _seq_id, seq in sequences))
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


def identity_from_fasta(path):
    """`(sequence_sha256, seq_ids, total_length)` for the FASTA at `path`, or None.

    None for anything unreadable or empty. At least one real deployment holds a reference
    row whose files never made it into the store, and a migration that raised on it would
    block every later migration on that instance.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            sequences = list(parse_fasta(handle))
    except (OSError, UnicodeDecodeError):
        return None
    if not sequences:
        return None
    entries = [{"id": seq_id, "length": len(seq), "sha256": sequence_digest(seq)}
               for seq_id, seq in sequences]
    return (sequence_set_digest(sequences), entries,
            sum(len(seq) for _seq_id, seq in sequences))

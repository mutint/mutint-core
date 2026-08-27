"""Whether a hand-entered mutation is well-formed, and whether it would do anything.

A mutation that arrives through `gd_import` was written by breseq and has read evidence behind
it. One typed into a form has neither, so this is the only thing between a person and a stored
mutation that is malformed, out of range, or a no-op -- a SNP whose new base is the base
already there changes nothing, and nothing downstream would ever notice.

Four stages, in increasing cost, and the order is load-bearing:

1. **Shape** -- the fields this type requires are present and parse. Needs nothing.
2. **Sequence-independent no-ops** -- an AMP to one copy is not an amplification. Needs
   nothing, so it is refused even on an experiment with no reference.
3. **Contig and bounds** -- read from `ExperimentReference.seq_ids`, which carries a `length`
   per contig, so this costs one already-loaded JSON column and no file access.
4. **Sequence no-ops** -- the only stage that needs the bases. Loading them parses the whole
   genome (seconds on a cold worker, memoised after), which is why `load_references` is a
   callable rather than a value: a DEL never triggers it.

Stage 4 must not run before stage 3. `ReferenceSequences.get_sequence_1` does no bounds
checking of its own -- an over-long end returns a short string and a start of 0 returns the
wrong bases entirely, both silently -- so an unchecked position produces a confident wrong
answer rather than an error.

Stages 3 and 4 are skipped when the experiment has no stored reference. That is a real state:
a `.gd` can be imported before a reference is established. The form says so rather than
pretending it validated.

The field sets come from `genomediff.records.TYPE_SPECIFIC_FIELDS`, which is what the parser
fills a record from and what `Record.__str__` serialises in order. Restating them here would
give the form a second opinion about what a MOB needs.
"""

import re

from genomediff.records import TYPE_SPECIFIC_FIELDS

from aledb_import.annotate.annotator import mutation_interval
from aledb_import.annotate.model import reverse_complement

#: The GenomeDiff mutation types, in the order the dropdown offers them: the ones people reach
#: for most, first. `GenomeDiff.read` classifies by type-string length -- three letters is a
#: mutation, two is evidence, four is validation -- which is also why `Mutation.mutation_type`
#: is `max_length=3`.
MUTATION_TYPES = ("SNP", "SUB", "DEL", "INS", "INV", "AMP", "MOB", "CON", "INT")

#: What each type is, for the dropdown and the field legend. breseq's own wording, from the
#: field documentation in the vendored `gdparse`.
TYPE_LABELS = {
    "SNP": "SNP — single base substitution",
    "SUB": "SUB — multiple base substitution",
    "DEL": "DEL — deletion",
    "INS": "INS — insertion",
    "INV": "INV — inversion",
    "AMP": "AMP — amplification",
    "MOB": "MOB — mobile element insertion",
    "CON": "CON — gene conversion",
    "INT": "INT — integration",
}

#: Per-field help, keyed by the spec's own field name. One entry per field, so a field shared
#: between types is explained once -- which is the same reason the form can carry values
#: between types without a mapping table.
FIELD_HELP = {
    "seq_id": "Reference sequence (contig) the mutation is on.",
    "position": "1-based position in that contig.",
    "new_seq": "The new base(s). A SNP takes exactly one; INS inserts after the position.",
    "size": "Number of reference bases the mutation covers, starting at the position.",
    "new_copy_number": "How many copies the amplified region ends up as. Two or more.",
    "repeat_name": "Name of the mobile element, as annotated in the reference.",
    "strand": "Orientation of the inserted element: 1 or -1.",
    "duplication_size": "Bases duplicated at the insertion site. May be 0 or negative.",
    "region": "Where the replacement sequence comes from, as seq_id:start-end.",
}

#: Fields whose value is an integer.
INTEGER_FIELDS = ("position", "size", "new_copy_number", "duplication_size", "strand")

#: Fields holding bases.
SEQUENCE_FIELDS = ("new_seq",)

#: The types whose validity depends on the actual bases. Everything else is decided by shape
#: and bounds alone, and must not trigger a reference load -- that parses the whole genome.
SEQUENCE_CHECKED_TYPES = ("SNP", "SUB", "INV", "CON", "INT", "MOB")

_SEQUENCE_RE = re.compile(r"^[ACGTN]+$")
#: `seq_id:start-end`. The contig name is greedy up to the last colon, because a name may
#: itself contain one; start and end are what must be numeric.
_REGION_RE = re.compile(r"^(?P<seq_id>.+):(?P<start>\d+)-(?P<end>\d+)$")


def field_names(mutation_type):
    """The positional fields this type takes, in the spec's declared order."""
    return TYPE_SPECIFIC_FIELDS.get(mutation_type, ())


def form_schema():
    """What the page needs to build and drive the form, as JSON-safe data.

    Handed to the template through `json_script` so the dropdown, the visible fields and the
    required-field check all read the one table. A field breseq adds to a type appears in the
    form without an edit here.
    """
    return {
        "types": [{"name": name,
                   "label": TYPE_LABELS.get(name, name),
                   "fields": list(field_names(name))}
                  for name in MUTATION_TYPES],
        "help": FIELD_HELP,
        "integer_fields": list(INTEGER_FIELDS),
        "sequence_fields": list(SEQUENCE_FIELDS),
    }


# --- stage 1: shape -------------------------------------------------------------------------


def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce_int(raw, field, errors):
    """An integer field, or None with an error recorded."""
    try:
        return int(str(raw[field]).strip())
    except (KeyError, TypeError, ValueError):
        errors[field] = "Must be a whole number."
        return None


def _coerce_sequence(raw, field, errors, single=False):
    """Bases, upper-cased. `single` for a SNP, which takes exactly one."""
    value = str(raw.get(field, "")).strip().upper()
    if not _SEQUENCE_RE.match(value):
        errors[field] = "Use only the bases A, C, G, T or N."
        return None
    if single and len(value) != 1:
        errors[field] = "A SNP changes exactly one base; use SUB for %d." % len(value)
        return None
    return value


def _coerce_region(raw, errors):
    """`seq_id:start-end` into (seq_id, start, end).

    start may exceed end: breseq reads a reversed region as the reverse complement, and gene
    conversion from the opposite strand is a real thing to want to record.
    """
    match = _REGION_RE.match(str(raw.get("region", "")).strip())
    if not match:
        errors["region"] = "Use seq_id:start-end, for example NC_000913:1000-2000."
        return None
    return match.group("seq_id"), int(match.group("start")), int(match.group("end"))


def _shape(raw, mutation_type, errors):
    """Coerce every field this type declares. Returns the attributes dict."""
    attributes = {}

    for field in field_names(mutation_type):
        if _blank(raw.get(field)):
            errors[field] = "Required for a %s." % mutation_type

    for field in field_names(mutation_type):
        if field in errors:
            continue
        if field in INTEGER_FIELDS:
            value = _coerce_int(raw, field, errors)
            if value is not None:
                attributes[field] = value
        elif field in SEQUENCE_FIELDS:
            value = _coerce_sequence(raw, field, errors, single=(mutation_type == "SNP"))
            if value is not None:
                attributes[field] = value
        elif field == "region":
            parsed = _coerce_region(raw, errors)
            if parsed is not None:
                attributes["region"] = "%s:%d-%d" % parsed
        else:
            attributes[field] = str(raw[field]).strip()

    # Ranges the spec constrains, checked only once the value is an integer.
    if attributes.get("position") is not None and attributes["position"] < 1:
        errors["position"] = "Positions are 1-based, so the first base is 1."
    if attributes.get("size") is not None and attributes["size"] < 1:
        errors["size"] = "A size of %d covers no bases." % attributes["size"]
    if attributes.get("strand") is not None and attributes["strand"] not in (1, -1):
        errors["strand"] = "Strand is 1 or -1."
    if attributes.get("new_copy_number") is not None and attributes["new_copy_number"] < 1:
        errors["new_copy_number"] = "A copy number below 1 is a deletion, not an AMP."

    return attributes


# --- stage 2: no-ops that need no reference -------------------------------------------------


def _sequence_independent_noop(attributes, mutation_type, errors):
    """The one case that is decidable without looking at any bases.

    Run even when the experiment has no reference, because it is still true there -- refusing
    it only when a reference happens to be stored would be an odd place to draw the line.
    """
    if mutation_type == "AMP" and attributes.get("new_copy_number") == 1:
        errors["new_copy_number"] = (
            "One copy is what is already there, so this would change nothing.")


# --- stage 3: contig and bounds, from the stored manifest ------------------------------------


def contig_lengths(reference_row):
    """{seq_id: length} from `ExperimentReference.seq_ids` -- no file access."""
    if reference_row is None:
        return {}
    return {entry.get("id"): entry.get("length")
            for entry in (reference_row.seq_ids or []) if entry.get("id")}


def _check_span(lengths, seq_id, start, end, errors, field, what):
    length = lengths.get(seq_id)
    if length is None:
        errors[field] = "%s is not a sequence in this experiment's reference." % seq_id
        return False
    if start < 1:
        errors[field] = "%s starts before the beginning of %s." % (what, seq_id)
        return False
    if end > length:
        errors[field] = ("%s runs past the end of %s, which is %d bases."
                         % (what, seq_id, length))
        return False
    return True


def _bounds(attributes, mutation_type, lengths, errors):
    """Whether the mutation, and any region it reads from, lie inside the reference."""
    record = dict(attributes, type=mutation_type)
    try:
        start, end = mutation_interval(record)
    except (TypeError, ValueError):
        return False

    ok = _check_span(lengths, attributes.get("seq_id"), start, end, errors,
                     "position", "This mutation")

    if mutation_type in ("CON", "INT") and "region" in attributes:
        parsed = _REGION_RE.match(attributes["region"])
        region_seq_id = parsed.group("seq_id")
        region_start = int(parsed.group("start"))
        region_end = int(parsed.group("end"))
        low, high = sorted((region_start, region_end))
        ok = _check_span(lengths, region_seq_id, low, high, errors,
                         "region", "The source region") and ok

    return ok


# --- stage 4: no-ops that need the bases -----------------------------------------------------


def _region_bases(references, region):
    """The replacement sequence a CON/INT reads, reverse-complemented if written backwards."""
    parsed = _REGION_RE.match(region)
    seq_id = parsed.group("seq_id")
    start = int(parsed.group("start"))
    end = int(parsed.group("end"))
    if start <= end:
        return references.get_sequence_1(seq_id, start, end)
    return reverse_complement(references.get_sequence_1(seq_id, end, start))


def _sequence_noop(attributes, mutation_type, references, errors):
    """Refuse a mutation whose result is the sequence that is already there.

    DEL and INS are absent on purpose: any size of deletion removes bases and any non-empty
    insertion adds them, so neither can be a no-op once the shape checks have passed.
    """
    seq_id = attributes.get("seq_id")
    position = attributes.get("position")

    if mutation_type == "SNP":
        current = references.get_sequence_1(seq_id, position, position)
        if current == attributes.get("new_seq"):
            errors["new_seq"] = (
                "Position %d on %s is already %s, so this would change nothing."
                % (position, seq_id, current))

    elif mutation_type == "SUB":
        end = position + attributes["size"] - 1
        current = references.get_sequence_1(seq_id, position, end)
        if current == attributes.get("new_seq"):
            errors["new_seq"] = (
                "%s is already the sequence at %s:%d-%d, so this would change nothing."
                % (current, seq_id, position, end))

    elif mutation_type == "INV":
        end = position + attributes["size"] - 1
        span = references.get_sequence_1(seq_id, position, end)
        if reverse_complement(span) == span:
            errors["size"] = (
                "%s:%d-%d is its own reverse complement, so inverting it would change "
                "nothing." % (seq_id, position, end))

    elif mutation_type in ("CON", "INT"):
        end = position + attributes["size"] - 1
        target = references.get_sequence_1(seq_id, position, end)
        source = _region_bases(references, attributes["region"])
        if source == target:
            errors["region"] = (
                "That region already matches %s:%d-%d, so this would change nothing."
                % (seq_id, position, end))

    elif mutation_type == "MOB":
        name = attributes.get("repeat_name")
        if not references.repeat_family_sequence(name, attributes.get("strand", 1)):
            errors["repeat_name"] = (
                "This experiment's reference annotates no repeat element named %s, so "
                "nothing would be inserted." % name)


# --- the whole thing --------------------------------------------------------------------------


def validate_record(raw, mutation_type, reference_row=None, load_references=None):
    """Check one hand-entered mutation. Returns `(attributes, errors)`.

    `attributes` is the coerced, spec-named field dict, and is only meaningful when `errors`
    is empty. `errors` maps a field name to a sentence written for the person who typed it.

    reference_row     `ExperimentReference` or None. None means the experiment has no stored
                      reference, and stages 3 and 4 are skipped -- the mutation is stored
                      unvalidated against any sequence, which the page says out loud.
    load_references   zero-argument callable returning `ReferenceSequences` or None, called
                      **only** if stage 4 is reached. Loading parses the whole genome, so a
                      DEL, whose validity never depends on the bases, must not pay for it.
    """
    errors = {}

    if mutation_type not in MUTATION_TYPES:
        return {}, {"mutation_type": "Choose a mutation type."}

    attributes = _shape(raw, mutation_type, errors)
    _sequence_independent_noop(attributes, mutation_type, errors)
    if errors or reference_row is None:
        return attributes, errors

    if not _bounds(attributes, mutation_type, contig_lengths(reference_row), errors):
        # Deliberately returning here rather than falling through. `get_sequence_1` slices a
        # plain string: an over-long end returns a short one and a start of 0 returns the
        # wrong bases, both without raising, so a no-op check on an out-of-range span would
        # answer confidently and wrongly.
        return attributes, errors

    if mutation_type not in SEQUENCE_CHECKED_TYPES:
        # A DEL removes bases and an INS adds them whatever they are, and an AMP was settled
        # in stage 2. None of them is worth a genome parse.
        return attributes, errors

    references = load_references() if load_references is not None else None
    if references is None or attributes.get("seq_id") not in references:
        return attributes, errors

    _sequence_noop(attributes, mutation_type, references, errors)
    return attributes, errors

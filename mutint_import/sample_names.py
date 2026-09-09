"""What a sample's filename says about where it belongs.

Every import path -- the CLI, the web `.gd` drop, a breseq folder -- ends up asking one
question of one string: does this name carry a population, a time point and a sample name,
and if so which. `gd_import.import_document_as_sample` asks it here so a `.gd` file and the
breseq directory of the same sample cannot answer differently.

Two shapes are recognized, and a name matching neither is auto-numbered by the caller.

**A-F-I-R**, four integers separated by dashes -- `3-30000-1-1` is population 3, time point
30000, sample `1-1`. Still strict: all four fields must be integers, so `Ara-1_500gen_762B`
cannot half-match it and land every non-conforming file on 1-1-1-1.

The last two fields are one thing in the database -- see `sample_label`. They are parsed
apart because the *name* separates them, and joined because a replicate was never a level
of anything.

**Three underscore-separated fields**, `Ara-2_500gen_763A` -> population `Ara-2`, time point
500, sample `763A`. What makes this expressible at all is that the population and the sample
name are text: `Ara-1` and `Ara+1` are two different LTEE populations and any rule reducing
them to an integer merges them.

The middle field is the exception, because `Sample.time_point` is genuinely a number -- the
Fixed row set orders by it and takes a population's last two. So its **trailing text is
stripped**: `500gen` is 500, and a field with no leading digits (`t0`) is not a time point,
so the whole name falls through to auto-numbering rather than being half-read.

Deliberately not recognized:

- **Two fields, or four.** Three is what makes the reading unambiguous -- with two there is
  no telling whether `Ara-2_500gen` omits the sample or the population, and four would have
  to guess which extra field is the replicate.
- **A trailer on the population or the sample.** They are labels: `763A` and `763B` are two
  clones from one time point and stripping either to `763` would file them as the same
  sample.
"""

import collections
import re

#: What a name of this shape is called, for the caller that has to decide whether the name
#: says anything the coordinate does not. `3-30000-1-1` says exactly the coordinate and
#: nothing else; `Ara-2_500gen_763A` is how a person refers to the sample.
SHAPE_AFIR = "afir"
SHAPE_TRIPLE = "triple"

#: `shape` is what distinguishes the two; the first four fields are what the name said.
#: `isolate` and `replicate` are still separate here because the *name* separates them --
#: `sample_label` below is where they become the one thing the database stores.
SampleIdentity = collections.namedtuple(
    "SampleIdentity", "population time_point name replicate shape")

#: Leading digits, then whatever the person appended: `500gen`, `1500`, `30000cd`.
_LEADING_NUMBER = re.compile(r"^(\d+)")


def parse_sample_identity(sample_name):
    """A `SampleIdentity` for a name that carries one, else None.

    Population and sample name come back as **text** and time point and replicate as **integers**, which is
    the shape of the columns behind them.
    """
    return _parse_afir(sample_name) or _parse_underscore_triple(sample_name)


def _parse_afir(sample_name):
    """The strict four-integer form.

    Strict on purpose. `util.parse_ale_name` used to read these fields with a bare
    `except: return 1`, so a name it could not read became population 1, time point 1, sample 1 -- and
    every non-conforming file in a drop landed on the same sample. It is gone; this answers
    None instead, and the caller auto-numbers.
    """
    fields = sample_name.split("-")
    if len(fields) < 4:
        return None
    try:
        numbers = [int(field) for field in fields[:4]]
    except ValueError:
        return None
    population, time_point, name, replicate = numbers
    return SampleIdentity(str(population), time_point, str(name), replicate, SHAPE_AFIR)


def _parse_underscore_triple(sample_name):
    """`Ara-2_500gen_763A`. See the module docstring for what is and is not recognized."""
    fields = sample_name.split("_")
    if len(fields) != 3:
        return None
    population, time_point, name = (field.strip() for field in fields)
    if not population or not name:
        return None
    match = _LEADING_NUMBER.match(time_point)
    if match is None:
        return None
    # `replicate` is None, not 1: this shape has no such field, and `sample_label` uses the
    # difference to decide whether the name gets a suffix. It was 1 while a replicate was a
    # row that had to exist.
    return SampleIdentity(population, int(match.group(1)), name, None, SHAPE_TRIPLE)


def sample_label(name, replicate):
    """What the sample is called within its flask: `1-2`, `763A`, `1-1`.

    The replicate used to be a row of its own between the isolate and the sequencing. It is
    not a level of anything -- only two code paths ever created a run and both made one per
    replicate -- so it is a suffix on the label instead, and `1-1500-1-1` and `1-1500-1-2`
    are two samples in one flask rather than one isolate with two rows beneath it.

    **The suffix is kept even when the replicate is 1.** Appending it only when it is not 1
    would make `1` and `1-2` siblings, which reads as two unrelated samples rather than two
    replicates of one -- and it would leave the rule depending on a *value* rather than on
    the shape of the name. So the label is exactly what the filename spelled.

    `replicate` is None for a name that carries no such field: the underscore triple
    (`Ara-2_500gen_763A`) says `763A` and nothing more, and inventing a `-1` for it would
    put a number in a label the person did not write.
    """
    if replicate is None:
        return str(name)
    return "%s-%s" % (name, replicate)


class SampleNameError(ValueError):
    """The three parts cannot make a name. Carries the field at fault, for a form to point at."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


#: What a part of a coordinate may contain when a *name* has to carry it.
#:
#: The same characters `mutint_breseq.views.SAMPLE_NAME_RE` allows in a whole name, applied to
#: each part: a composed name is a directory name in that plugin, and no part of one should be
#: able to introduce a space, a separator or a leading dot. It is narrower than the column --
#: `Population.name` is a CharField and a dropped folder can create one with a space in it --
#: because what is narrow here is the *name*, not the place it is stored.
#:
#: Underscores are allowed, and the round-trip check below is what makes that safe.
_NAME_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def compose_sample_name(population, time_point, sample):
    """The three parts as the one name every import path reads back. The inverse of
    `parse_sample_identity`, and the reason both live here.

    **A name carries all three parts or none of them.** There is no spelling for a population
    with no time point -- the triple shape needs digits in the middle field -- so:

        Ara-2, 500, 763A    ->  "Ara-2_500_763A"   placed
        (none), (none), s1  ->  "s1"               auto-numbered: Unspecified, no time point
        Ara-2, (none), 763A ->  SampleNameError

    **Underscores, always.** The dash shape would need a fourth integer field and would only
    ever apply to a wholly numeric coordinate; one composer is worth more than a label that
    reads `3 / 30000 / 1-1` for some samples and `3_30000_1-1` for others.

    **The answer is checked by reading it back**, which is what lets a part contain an
    underscore at all: `x_5_y` as a sample name with nothing else filled in composes to a
    string the parser would read as a whole coordinate, and `Ara_2` as a population composes
    to four fields and loses one. Neither is caught by any rule about characters, and both are
    caught by asking `parse_sample_identity` what the composed name says and refusing when
    that is not what was asked for. The two can therefore never disagree.

    Callers pass the parts, not a joined string, so this convention can change without every
    form changing with it.
    """
    parts = {
        "population": ("" if population is None else str(population)).strip(),
        "time_point": ("" if time_point is None else str(time_point)).strip(),
        "sample": ("" if sample is None else str(sample)).strip(),
    }

    if not parts["sample"]:
        raise SampleNameError("sample", "A sample name is required.")

    labels = {"population": "population", "time_point": "time point", "sample": "sample name"}
    for field in ("population", "sample"):
        if parts[field] and not _NAME_PART.match(parts[field]):
            raise SampleNameError(
                field,
                "A %s may use letters, digits, dot, underscore, plus and hyphen, and must "
                "start with a letter or digit. It becomes part of this sample's name, which "
                "is also a directory name." % labels[field])

    if parts["time_point"] and not parts["time_point"].isdigit():
        # Leading digits are all `parse_sample_identity` reads, so `12.5` would come back as
        # 12 and `t0` would lose the coordinate altogether. `Sample.time_point` is a float and
        # holds more than this -- what is narrower is the name, not the column.
        raise SampleNameError(
            "time_point",
            "A time point in a name must be a whole number. Leave it empty for a sample "
            "nobody has placed yet.")

    if bool(parts["population"]) != bool(parts["time_point"]):
        missing = "time point" if parts["population"] else "population"
        raise SampleNameError(
            "time_point" if parts["population"] else "population",
            "A name carries a population and a time point together or neither: give a %s "
            "as well, or clear both to leave the sample unplaced." % missing)

    placed = bool(parts["population"])
    name = ("_".join((parts["population"], parts["time_point"], parts["sample"]))
            if placed else parts["sample"])

    _check_round_trip(name, parts, placed)
    return name


def _check_round_trip(name, parts, placed):
    """Refuse a name that does not say what the parts said. See `compose_sample_name`."""
    identity = parse_sample_identity(name)

    if not placed:
        if identity is not None:
            raise SampleNameError(
                "sample",
                "%r would be read as population %s, time point %s and sample %s. Fill the "
                "population and time point in, or take the underscores out of the name."
                % (name, identity.population, identity.time_point,
                   sample_label(identity.name, identity.replicate)))
        return

    read_back = None if identity is None else (
        identity.population, str(identity.time_point),
        sample_label(identity.name, identity.replicate))
    wanted = (parts["population"], str(int(parts["time_point"])), parts["sample"])
    if read_back != wanted:
        raise SampleNameError(
            "population",
            "%r would not be read back as this coordinate -- an underscore in the population "
            "or the sample splits the name into the wrong parts. Use a hyphen instead."
            % name)

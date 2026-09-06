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

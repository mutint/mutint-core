"""What a sample's filename says about where it belongs.

Every import path -- the CLI, the web `.gd` drop, a breseq folder -- ends up asking one
question of one string: does this name carry an ALE, a time point and an isolate, and if so
which. `gd_import.import_document_as_sample` asks it here so a `.gd` file and the breseq
directory of the same sample cannot answer differently.

Two shapes are recognised, and a name matching neither is auto-numbered by the caller.

**A-F-I-R**, four integers separated by dashes -- `3-30000-1-1` is ALE 3, flask 30000,
isolate 1, replicate 1. Unchanged, and still strict: all four fields must be integers, so
`Ara-1_500gen_762B` cannot half-match it and land every non-conforming file on 1-1-1-1.

**Three underscore-separated fields**, `Ara-2_500gen_763A` -> ALE `Ara-2`, flask 500,
isolate `763A`. What makes this expressible at all is that the ALE and the isolate are text
(`aledb_experiment.0008`): `Ara-1` and `Ara+1` are two different LTEE populations and any
rule reducing them to an integer merges them.

The middle field is the exception, because `Flask.flask_number` is genuinely a number --
aledb-fixation sorts by it and takes an ALE's last two flasks. So its **trailing text is
stripped**: `500gen` is 500, and a field with no leading digits (`t0`) is not a time point,
so the whole name falls through to auto-numbering rather than being half-read.

Deliberately not recognised:

- **Two fields, or four.** Three is what makes the reading unambiguous -- with two there is
  no telling whether `Ara-2_500gen` omits the isolate or the ALE, and four would have to
  guess which extra field is the replicate.
- **A trailer on the ALE or the isolate.** They are labels: `763A` and `763B` are two clones
  from one flask and stripping either to `763` would file them as the same isolate, which
  is the collision `aledb-fixation` cannot see -- it builds a dict keyed by
  `(flask_number, isolate_number)` by plain assignment.
"""

import collections
import re

#: What a name of this shape is called, for the caller that has to decide whether the name
#: says anything the coordinate does not. `3-30000-1-1` says exactly the coordinate and
#: nothing else; `Ara-2_500gen_763A` is how a person refers to the sample.
SHAPE_AFIR = "afir"
SHAPE_TRIPLE = "triple"

#: `shape` is what distinguishes the two; the first four fields are the coordinate.
SampleIdentity = collections.namedtuple(
    "SampleIdentity", "ale flask isolate replicate shape")

#: Leading digits, then whatever the person appended: `500gen`, `1500`, `30000cd`.
_LEADING_NUMBER = re.compile(r"^(\d+)")


def parse_sample_identity(sample_name):
    """A `SampleIdentity` for a name that carries one, else None.

    ALE and isolate come back as **text** and flask and replicate as **integers**, which is
    the shape of the columns behind them.
    """
    return _parse_afir(sample_name) or _parse_underscore_triple(sample_name)


def _parse_afir(sample_name):
    """The strict four-integer form.

    Strict on purpose. `util.parse_ale_name` used to read these fields with a bare
    `except: return 1`, so a name it could not read became ALE 1, flask 1, isolate 1 -- and
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
    ale, flask, isolate, replicate = numbers
    return SampleIdentity(str(ale), flask, str(isolate), replicate, SHAPE_AFIR)


def _parse_underscore_triple(sample_name):
    """`Ara-2_500gen_763A`. See the module docstring for what is and is not recognised."""
    fields = sample_name.split("_")
    if len(fields) != 3:
        return None
    ale, time_point, isolate = (field.strip() for field in fields)
    if not ale or not isolate:
        return None
    match = _LEADING_NUMBER.match(time_point)
    if match is None:
        return None
    return SampleIdentity(ale, int(match.group(1)), isolate, 1, SHAPE_TRIPLE)

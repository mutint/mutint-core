"""The ORM traversals from a mutation or a sample up to the experiment that owns it.

Every table in the suite hangs off one chain::

    ObservedMutation -> ResequencingExperiment -> Flask -> AleId -> AleExperiment

so almost every query has to spell some part of it as a lookup string. Before this module
there were **39 hand-written copies** of that spelling across eight core files and three
plugin repositories, sharing nothing. A rename anywhere along the chain meant finding all 39,
and a missed one fails the way lookup strings fail: `FieldError` if you are lucky, a filter
that silently matches nothing if you are not.

**The reason to have this before renaming anything.** Two different columns on this chain are
both called ``ale_id`` -- `AleExperiment`'s *primary key* and `AleId`'s *text label* -- so
"the path ending in ``ale_id``" is two different destinations depending on where it started.
Naming them apart here (`EXPERIMENT_PK`, `ALE_LABEL`) is what turns that rename from an
audit of 39 strings into an edit of one constant, and it is why the two are separate names
even though they are the same word today.

**The chain used to be two segments longer.** `Isolate` and `TechnicalReplicate` sat between
the sample and its flask, so this read
``tech_rep__isolate__flask__ale_id__ale_experiment``; both are folded into the sample now.
That is why `to_isolate()` and `to_replicate()` are gone rather than renamed -- what they
reached is on the sample itself, so `to_sample()` is the whole of it and usually resolves to
nothing at all.

Usage::

    from aledb_experiment.paths import FROM_OBSERVATION, to_experiment_id, to_ale

    ObservedMutation.objects.filter(**{to_experiment_id(FROM_OBSERVATION): experiment_id})
    ResequencingExperiment.objects.select_related(to_ale())

`prefix` is what the queryset starts from: nothing when it starts at a sample, and
`FROM_OBSERVATION` when it starts at an observation. Anything else -- the mutation editor's
change log reaches a sample as ``sample`` and ``changes__sample`` -- passes its own.
"""

#: `ObservedMutation` reaches a sample through this FK. Named because it is itself a rename
#: candidate: the model is `ResequencingExperiment`, the product calls it a sample, and this
#: field is spelled as neither.
FROM_OBSERVATION = "sequencing_experiment"

#: `MutationChange` already calls it what the product calls it.
FROM_CHANGE = "sample"

#: The chain, segment by segment, from a sample up to the experiment. Everything below is
#: composed from this, so a queryset that starts **part-way along** it gets the same
#: definition rather than a second hand-written one.
SEGMENTS = ("flask", "ale_id", "ale_experiment")

#: Where a queryset starts, as a position in SEGMENTS. `prefix` is for roots that sit
#: *before* a sample -- an observation, a change-log row -- and `root` for roots along the
#: chain itself.
ROOTS = {"sample": 0, "flask": 1, "ale": 2}


def chain(root="sample", upto="ale_experiment"):
    """The lookup path from `root` up to and including `upto`."""
    return "__".join(SEGMENTS[ROOTS[root]:SEGMENTS.index(upto) + 1])


#: A sample to the flask it was drawn from.
TO_FLASK = chain(upto="flask")

#: ...to the ALE lineage.
TO_ALE = chain(upto="ale_id")

#: ...to the experiment.
TO_EXPERIMENT = chain()

#: `AleExperiment`'s primary key -- now Django's implicit `id`, like every other table. It
#: was `ale_id`, the same word as `ALE_LABEL` below, and this constant is what made changing
#: it an edit of one line rather than an audit of every lookup in the suite.
EXPERIMENT_PK = "id"

#: `AleId`'s text label: `Ara-1`, `3`. It shared its spelling with the experiment's primary
#: key until that was renamed, which is the confusion this module was written against.
ALE_LABEL = "ale_id"

#: `Flask`'s ordinal. Labelled "Time point" everywhere a person can see it.
FLASK_ORDINAL = "flask_number"

#: The sample's label within its flask: `1`, `763A`, `1-2`. Still spelled `isolate_number`,
#: which is the column `Isolate` brought with it into the merge.
SAMPLE_LABEL = "isolate_number"


#: The same chain read **downward**. Django spells a reverse relation with the lowercased
#: model name, because none of these declares a `related_name` -- so it is not simply
#: `SEGMENTS` reversed, and that is exactly why three files hand-wrote it independently
#: (`aledb_dashboard/util.py`, `aledb_seq/views/common.py`, `aledb-phylogeny/selection.py`)
#: while every upward traversal in the suite came from here.
DOWN_SEGMENTS = ("aleid", "flask", "resequencingexperiment")

#: Where a downward traversal starts, as a position in DOWN_SEGMENTS.
DOWN_ROOTS = {"experiment": 0, "ale": 1, "flask": 2}


def down_chain(root="ale", upto="resequencingexperiment"):
    """The reverse lookup path from `root` down to and including `upto`."""
    return "__".join(DOWN_SEGMENTS[DOWN_ROOTS[root]:DOWN_SEGMENTS.index(upto) + 1])


def join(*steps):
    """Lookup path from `steps`, skipping empty ones so a prefix may be omitted.

    Trailing and leading separators are trimmed off each step, because callers reasonably
    write a prefix the way the old string concatenation wanted it -- `"sequencing_experiment__"`
    -- and joining that naively yields four underscores, which Django reports as
    ``Unsupported lookup '' for BigAutoField``. Tolerating both spellings costs one strip
    and removes a failure that says nothing about its cause.
    """
    return "__".join(step.strip("_") for step in steps if step and step.strip("_"))


def to_sample(prefix="", field=""):
    """A field on the sample row itself.

    From a sample-rooted queryset this is just the field name, so the call looks like it is
    doing nothing -- and that is the point. It used to be `to_isolate()`, a real two-segment
    hop; the merge made those columns local without making the call sites wrong.
    """
    return join(prefix, field)


def to_sample_label(prefix=""):
    return join(prefix, SAMPLE_LABEL)


def to_flask(prefix="", field="", root="sample"):
    return join(prefix, chain(root, "flask"), field)


def to_ale(prefix="", field="", root="sample"):
    return join(prefix, chain(root, "ale_id"), field)


def to_experiment(prefix="", field="", root="sample"):
    return join(prefix, chain(root), field)


def to_experiment_id(prefix="", root="sample"):
    """The experiment's primary key -- what `?ale_experiment_id=` carries."""
    return join(prefix, chain(root), EXPERIMENT_PK)


def to_ale_label(prefix="", root="sample"):
    """The ALE's label, which is *not* the experiment's pk however alike they read."""
    return join(prefix, chain(root, "ale_id"), ALE_LABEL)


def to_flask_ordinal(prefix="", root="sample"):
    return join(prefix, chain(root, "flask"), FLASK_ORDINAL)

"""The ORM traversals from a mutation or a sample up to the experiment that owns it.

Every table in the suite hangs off one chain::

    MutationCall -> Sample -> Population -> Experiment

so almost every query has to spell some part of it as a lookup string. Before this module
there were **39 hand-written copies** of that spelling across eight core files and three
plugin repositories, sharing nothing. A rename anywhere along the chain meant finding all 39,
and a missed one fails the way lookup strings fail: `FieldError` if you are lucky, a filter
that silently matches nothing if you are not.

**This module is why the rename was possible.** Two different columns on this chain were
both called ``ale_id`` -- the experiment's *primary key* and the population's *text label* --
so "the path ending in ``ale_id``" was two different destinations depending on where it
started. Naming them apart here (`EXPERIMENT_PK`, `POPULATION_LABEL`) turned each rename from
an audit of 39 strings into an edit of one constant. They are no longer the same word, and
the constants stay because the next rename will want them.

**The chain used to be twice as long.** `Isolate` and `TechnicalReplicate` sat between the
sample and its time point, so this read
``tech_rep__isolate__flask__ale_id__ale_experiment``; both are folded into the sample. That
is why `to_isolate()` and `to_replicate()` are gone rather than renamed -- what they reached
is on the sample itself, so `to_sample()` is the whole of it and usually resolves to nothing
at all.

Usage::

    from mutint_experiment.paths import FROM_CALL, to_experiment_id, to_population

    MutationCall.objects.filter(**{to_experiment_id(FROM_CALL): experiment_id})
    Sample.objects.select_related(to_population())

`prefix` is what the queryset starts from: nothing when it starts at a sample, and
`FROM_CALL` when it starts at a call. Anything else -- the mutation editor's
edit log reaches a sample as ``sample`` and ``edits__sample`` -- passes its own.
"""

#: `MutationCall` reaches a sample through this FK. It was `sample`, on a
#: model called `Sample`, while the product called the thing a sample -- the
#: field was spelled as neither. All three agree now, and the constant stays because a
#: prefix is still what a caller starting at a call has to pass.
FROM_CALL = "sample"

#: `MutationEdit` called it this before anything else did.
FROM_EDIT = "sample"

#: The chain, segment by segment, from a sample up to the experiment. Everything below is
#: composed from this, so a queryset that starts **part-way along** it gets the same
#: definition rather than a second hand-written one.
SEGMENTS = ("population", "experiment")

#: Where a queryset starts, as a position in SEGMENTS. `prefix` is for roots that sit
#: *before* a sample -- a call, a change-log row -- and `root` for roots along the
#: chain itself.
ROOTS = {"sample": 0, "population": 1}


def chain(root="sample", upto="experiment"):
    """The lookup path from `root` up to and including `upto`."""
    return "__".join(SEGMENTS[ROOTS[root]:SEGMENTS.index(upto) + 1])


#: A sample to the population it belongs to.
TO_POPULATION = chain(upto="population")

#: ...to the experiment.
TO_EXPERIMENT = chain()

#: `Experiment`'s primary key -- Django's implicit `id`, like every other table. It was
#: `ale_id`, the same word as `POPULATION_LABEL` below, and this constant is what made
#: changing it an edit of one line rather than an audit of every lookup in the suite.
EXPERIMENT_PK = "id"

#: `Population`'s text label: `Ara-1`, `3`. It shared its spelling with the experiment's
#: primary key until that was renamed, which is the confusion this module was written
#: against.
POPULATION_LABEL = "name"

#: The sample's ordinal along its population's history. It was `TimePoint.value` on a row of
#: its own and before that `TimePoint.time_point`; it is a column on the sample now, so
#: this is a field name rather than the tail of a chain -- which is why `to_time_point_value`
#: below composes it with `to_sample` and no longer takes a `root`.
TIME_POINT_VALUE = "time_point"

#: The sample's label within its time point: `1`, `763A`, `1-2`.
SAMPLE_LABEL = "name"

#: Whether the sample is one genotype. It was `is_population`, with the opposite meaning.
SAMPLE_CLONAL = "is_clonal"


#: The same chain read **downward**. Django spells a reverse relation with the lowercased
#: model name, because none of these declares a `related_name` -- so it is not simply
#: `SEGMENTS` reversed, and that is exactly why three files hand-wrote it independently
#: (`mutint_dashboard/util.py`, `mutint_sample/views/common.py`, `mutint-phylogeny/selection.py`)
#: while every upward traversal in the suite came from here.
DOWN_SEGMENTS = ("population", "sample")

#: Where a downward traversal starts, as a position in DOWN_SEGMENTS.
DOWN_ROOTS = {"experiment": 0, "population": 1}


def down_chain(root="population", upto="sample"):
    """The reverse lookup path from `root` down to and including `upto`."""
    return "__".join(DOWN_SEGMENTS[DOWN_ROOTS[root]:DOWN_SEGMENTS.index(upto) + 1])


def join(*steps):
    """Lookup path from `steps`, skipping empty ones so a prefix may be omitted.

    Trailing and leading separators are trimmed off each step, because callers reasonably
    write a prefix the way the old string concatenation wanted it -- `"sample__"`
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


def clonal_filter(prefix=""):
    """Filter kwargs selecting the clonal samples.

    A pair with `mixed_filter` rather than one helper taking a boolean, and neither takes a
    `not`. The column this replaced was `is_population`, so every one of these call sites had
    its polarity inverted at once -- and a filter written `is_clonal=False` reviews as
    arithmetic, where `mixed_filter()` reviews as a word you can check against the sentence
    around it. That is the only kind of review that reliably catches a flipped flag.
    """
    return {to_sample(prefix, SAMPLE_CLONAL): True}


def mixed_filter(prefix=""):
    """Filter kwargs selecting the mixed (population) samples. See `clonal_filter`."""
    return {to_sample(prefix, SAMPLE_CLONAL): False}


def to_population(prefix="", field="", root="sample"):
    return join(prefix, chain(root, "population"), field)


def to_experiment(prefix="", field="", root="sample"):
    return join(prefix, chain(root), field)


def to_experiment_id(prefix="", root="sample"):
    """The experiment's primary key -- what `?experiment_id=` carries."""
    return join(prefix, chain(root), EXPERIMENT_PK)


def to_population_label(prefix="", root="sample"):
    """The population's label. It is *not* the experiment's pk, however alike the two read
    before the rename -- both were spelled `ale_id`."""
    return join(prefix, chain(root, "population"), POPULATION_LABEL)


def to_time_point_value(prefix=""):
    """The sample's time point -- a column on the sample, not a hop.

    It kept its name through the `TimePoint` removal on purpose: every caller wanted the
    *value* all along, and the ones that had to spell the hop themselves are exactly the
    ones this module exists to keep out of. There is no `root` any more, because there is
    no row above the sample this could be rooted at."""
    return join(prefix, TIME_POINT_VALUE)

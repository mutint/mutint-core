"""The ORM traversals from a mutation or a sample up to the experiment that owns it.

Every table in the suite hangs off one chain::

    ObservedMutation -> ResequencingExperiment -> TechnicalReplicate
                     -> Isolate -> Flask -> AleId -> AleExperiment

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

#: A sample to the flask it was drawn from.
TO_FLASK = "tech_rep__isolate__flask"

#: ...to the ALE lineage.
TO_ALE = TO_FLASK + "__ale_id"

#: ...to the experiment.
TO_EXPERIMENT = TO_ALE + "__ale_experiment"

#: `AleExperiment`'s primary key. There is no `id` on that model -- this *is* the pk -- which
#: is why it reads like a column name rather than like one.
EXPERIMENT_PK = "ale_id"

#: `AleId`'s text label: `Ara-1`, `3`. **The same word as EXPERIMENT_PK above and a different
#: column**, which is the whole reason this module exists.
ALE_LABEL = "ale_id"

#: `Flask`'s ordinal. Labelled "Time point" everywhere a person can see it.
FLASK_ORDINAL = "flask_number"


def join(*steps):
    """Lookup path from `steps`, skipping empty ones so a prefix may be omitted.

    Trailing and leading separators are trimmed off each step, because callers reasonably
    write a prefix the way the old string concatenation wanted it -- `"sequencing_experiment__"`
    -- and joining that naively yields four underscores, which Django reports as
    ``Unsupported lookup '' for BigAutoField``. Tolerating both spellings costs one strip
    and removes a failure that says nothing about its cause.
    """
    return "__".join(step.strip("_") for step in steps if step and step.strip("_"))


def to_flask(prefix="", field=""):
    return join(prefix, TO_FLASK, field)


def to_ale(prefix="", field=""):
    return join(prefix, TO_ALE, field)


def to_experiment(prefix="", field=""):
    return join(prefix, TO_EXPERIMENT, field)


def to_experiment_id(prefix=""):
    """The experiment's primary key -- what `?ale_experiment_id=` carries."""
    return join(prefix, TO_EXPERIMENT, EXPERIMENT_PK)


def to_ale_label(prefix=""):
    """The ALE's label, which is *not* the experiment's pk however alike they read."""
    return join(prefix, TO_ALE, ALE_LABEL)


def to_flask_ordinal(prefix=""):
    return join(prefix, TO_FLASK, FLASK_ORDINAL)

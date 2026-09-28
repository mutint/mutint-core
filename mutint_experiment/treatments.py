"""An experiment's treatments: the labels its samples carry, counted, renamed, and checked.

The treatment is a column on `Sample` and not a row of its own (see `Sample.treatment`), so
"the experiment's treatments" is a question answered from the samples: which distinct
labels are in use and by how many. That is also what makes the list maintainable without a
model -- renaming a treatment is one UPDATE over the samples carrying it, and a typo that
made a second level is mended the same way.

`mixed_populations` is the check that comes with storing the factor per sample. In an ALE a
treatment is usually a property of the lineage, and nothing here enforces that a population's
samples agree; the experiment page says so instead, naming the populations whose samples
carry more than one, so the choice to split one is a visible one.
"""

from django.db.models import Count

from mutint_experiment import paths
from mutint_experiment.samples import _MAX_LENGTHS, SampleEditError


def treatments_in_use(experiment):
    """`[{"name": ..., "count": n}, ...]`, every non-blank label, natural order."""
    from mutint_experiment.ordering import PAD
    from mutint_sample.models import Sample

    field = paths.to_sample_treatment()
    rows = (Sample.objects.filter(**{paths.to_experiment_id(): experiment.pk})
            .exclude(**{field + "__isnull": True})
            .exclude(**{field: ""})
            .values(field)
            .annotate(count=Count("id"))
            .order_by())
    entries = [{"name": row[field], "count": row["count"]} for row in rows]
    return sorted(entries, key=lambda entry: entry["name"].rjust(PAD, "0"))


def mixed_populations(experiment):
    """Populations whose samples carry more than one treatment (blank counting as one).

    `[{"population": name, "treatments": [labels...]}, ...]`, in population order; empty
    when every population is uniform, which is the usual case and the one the page then
    says nothing about.
    """
    from mutint_experiment.ordering import natural
    from mutint_sample.models import Sample

    field = paths.to_sample_treatment()
    label = paths.to_population_label()
    rows = (Sample.objects.filter(**{paths.to_experiment_id(): experiment.pk})
            .values(label, field)
            .distinct()
            .order_by(natural(label)))
    by_population = {}
    order = []
    for row in rows:
        name = row[label]
        if name not in by_population:
            by_population[name] = []
            order.append(name)
        by_population[name].append(row[field] or "")
    return [{"population": name, "treatments": sorted(by_population[name])}
            for name in order if len(by_population[name]) > 1]


def rename_treatment(experiment, old, new):
    """Give every sample carrying `old` the label `new`; blank `new` clears it.

    Returns how many samples changed. Descriptive in `mutint_experiment.samples`' sense --
    no row moves and nothing derived reads the label -- so nothing is rebuilt. A `new` that is
    already in use merges the two levels, which is what renaming a typo onto the word it
    meant asks for.
    """
    from mutint_sample.models import Sample

    old = (old or "").strip()
    new = (new or "").strip()
    if not old:
        raise SampleEditError("Name the treatment to rename.")
    if len(new) > _MAX_LENGTHS["treatment"]:
        raise SampleEditError("A treatment is at most %d characters." % _MAX_LENGTHS["treatment"])
    return (Sample.objects
            .filter(**{paths.to_experiment_id(): experiment.pk, paths.to_sample_treatment(): old})
            .update(**{paths.to_sample_treatment(): new}))

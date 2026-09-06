"""How a list of samples is ordered, now that two thirds of a coordinate are text.

`Population.name` and the sample's `name` are `CharField`s
(`mutint_experiment.0008`), so
the database orders them the way it orders any string: `10` before `2`, and `A1 F1 I10`
above `A1 F1 I2` in every mutation table. That is not a cosmetic difference on real data --
an auto-numbered import gives one time point a sample apiece, and the dev database's
largest experiment has fifty-one of them.

`sample_order()` is what every sample listing orders by instead. It pads each text field
left with zeros to a fixed width, so a run of digits sorts by its value and anything else
keeps sorting as text:

    "2"     -> "00000000000000000002"
    "10"    -> "00000000000000000010"     after "2", as it should be
    "763A"  -> "0000000000000000763A"     after "763", before "764"
    "Ara-1" -> "000000000000000Ara-1"     letters after digits, stably

Two things it does not claim. A label longer than `PAD` is truncated *for sorting only* --
`LPAD` truncates rather than overflows, so two labels agreeing in their first twenty
characters sort as equal and fall through to the next key. And a number written with a
leading zero (`007`) sorts as if it were `7`, which is what anybody would want and is worth
saying out loud because it is the one case where two distinct labels sort as one.

`LPad` and `Cast` are both portable: MySQL has `LPAD` natively and Django registers it as a
custom function on SQLite, which is what the test suite runs on.
"""

from django.db.models import CharField, F, Value
from django.db.models.functions import Cast, LPad

from mutint_experiment import paths

#: Wide enough for a time point in cumulative divisions (five figures) and for the kind of
#: label a lineage carries. Sorting-only, so widening it later changes no stored data.
PAD = 20



def natural(field_path):
    """One text field, ordered as a person would read it. See the module docstring."""
    return LPad(Cast(field_path, CharField()), PAD, Value("0"))


def sample_order(prefix=""):
    """The four keys a sample list is ordered by: experiment, ALE, time point, sample label.

    Pass `prefix="sample__"` from a queryset of `MutationCall`. Returns
    a tuple for `order_by(*sample_order())` -- expressions rather than field names, because
    two of the five need the padding above.

    The time point needs no padding -- it is numeric and the database orders it
    correctly -- but it *is* nullable, so it needs `nulls_first`. Without it the answer is
    the backend's: PostgreSQL sorts NULLs last ascending, SQLite and MySQL sort them first,
    so a sample with no time point would land in a different place in production than in the test
    suite. Stated rather than inherited, and first because a sample with no time point is
    one nobody has placed yet.
    """
    return (
        F(paths.to_experiment(prefix, "name")),
        natural(paths.to_population_label(prefix)),
        F(paths.to_time_point_value(prefix)).asc(nulls_first=True),
        natural(paths.to_sample_label(prefix)),
    )


def sample_sort_key(sample):
    """A sample's A/F/I coordinate as a sortable tuple, for a list already in memory.

    `sample_order()` above is the database form and is what every listing uses. This is for
    the two places that cannot re-query: the CSV export, which has collapsed calls
    into the samples that appear in them, and the mutation editor's history tally, which has
    collapsed changes into a per-sample count.

    **It has to agree with `natural()`**, so it pads the same two text fields the same way --
    that is what puts `F2` before `F10`, and it is the whole reason this exists rather than
    `sorted(..., key=lambda r: r.label)`, which is a lexicographic sort over
    a *display* string and gets both the numbers and, wherever an isolate description is set,
    the field itself wrong.

    A null time point sorts first, as it does in the SQL form.
    """
    population = sample.population
    return (population.experiment.name,
            str(population.name).rjust(PAD, "0"),
            sample.time_point if sample.time_point is not None else -1,
            str(sample.name).rjust(PAD, "0"))

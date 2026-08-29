"""How a list of samples is ordered, now that two thirds of a coordinate are text.

`AleId.ale_id` and `Isolate.isolate_number` are `CharField`s (`aledb_experiment.0008`), so
the database orders them the way it orders any string: `10` before `2`, and `A1 F1 I10`
above `A1 F1 I2` in every mutation table. That is not a cosmetic difference on real data --
an auto-numbered import gives one flask an isolate per sample, and the dev database's
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

#: Wide enough for a flask number in cumulative divisions (five figures) and for the kind of
#: label a lineage carries. Sorting-only, so widening it later changes no stored data.
PAD = 20

#: The chain from a `ResequencingExperiment` up to its experiment. Every consumer is either
#: rooted there or one step below it on `ObservedMutation`, which is what `prefix` is for.
_CHAIN = "tech_rep__isolate__flask__ale_id__"


def natural(field_path):
    """One text field, ordered as a person would read it. See the module docstring."""
    return LPad(Cast(field_path, CharField()), PAD, Value("0"))


def sample_order(prefix=""):
    """The five keys a sample list is ordered by: experiment, ALE, flask, isolate, replicate.

    Pass `prefix="sequencing_experiment__"` from a queryset of `ObservedMutation`. Returns
    a tuple for `order_by(*sample_order())` -- expressions rather than field names, because
    two of the five need the padding above.

    The flask is a plain `F()`: it is still an `IntegerField` and the database already
    orders it correctly.
    """
    chain = prefix + _CHAIN
    return (
        F(chain + "ale_experiment__name"),
        natural(chain + "ale_id"),
        F(prefix + "tech_rep__isolate__flask__flask_number"),
        natural(prefix + "tech_rep__isolate__isolate_number"),
        F(prefix + "tech_rep__tech_rep_number"),
    )

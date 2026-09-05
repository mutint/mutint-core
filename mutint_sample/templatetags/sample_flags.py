"""`{% sample_flag_badges sample %}` -- the badges a flagged sample carries wherever it is named.

One partial for the Mutations page picker, the stats table and the mutation matrix, so a sample
marked contaminated looks the same everywhere. Takes a `Sample`, or anything else with the flag
attributes (the matrix's `SampleColumn` carries the resolved flags instead -- pass `flags=`).
"""

from django import template

from mutint_sample.flags import flags_of

register = template.Library()


@register.inclusion_tag("sample/_flags.html")
def sample_flag_badges(sample=None, flags=None):
    return {"flags": list(flags) if flags is not None else flags_of(sample)}

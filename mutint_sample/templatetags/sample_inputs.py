"""Rendering helpers for the "what this sample was made from" box.

Filters rather than context keys, for the reason `view_filter`'s tags are: the box is
included from several pages and none of them should have to remember to compute anything
for it.
"""

from django import template

from mutint_experiment.coordinates import format_time_point
from mutint_sample import inputs

register = template.Library()


@register.filter(name="time_point")
def time_point(value):
    """`500`, not `500.0` -- `coordinates.format_time_point`, reachable from a template."""
    formatted = format_time_point(value)
    return "" if formatted is None else formatted


@register.filter(name="by_group")
def by_group(entries):
    """Entries gathered into the groups they were recorded in, in order.

    Mates of a pair share a group and belong on one line: *Read file a_R1.fastq, a_R2.fastq
    paired*. Everything else is a group of one and reads the same way without the word.

    The label is the group's kind rather than each entry's, because a group is one input seen
    as two files; a group whose entries somehow disagree takes the first, which is a shape
    nothing writes and not worth a branch beyond not crashing on it.
    """
    ordered = []
    seen = {}
    for entry in entries or []:
        key = entry.get("group")
        if key not in seen:
            seen[key] = {"label": inputs.label_for(entry.get("kind")), "entries": []}
            ordered.append(seen[key])
        seen[key]["entries"].append(dict(entry, url=inputs.url_for(entry)))
    for group in ordered:
        group["paired"] = len(group["entries"]) > 1
    return ordered

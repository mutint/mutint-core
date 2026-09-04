"""How a sample's coordinate is written, in one place.

Three parts -- population, time point, sample label -- and until now the format string for
them existed twice, in `aledb_sample.models.Sample` and in `aledb_experiment.samples`, kept in
step by hand. They had already drifted once: the model's was rebuilt when the replicate
folded into the label and the other was edited separately to match.

**The separator is ` / `.** Every shorter candidate is taken by the data it would separate:

- `-` and `_` both occur *inside* real values. `Ara-1` is a population name and
  `1-2` is a sample label; `Ara-1_500gen_762B` is a filename people paste as a description.
  Either separator would split in the middle of a part, and a reader could not tell which.
- `,` would need quoting in the CSV export, where these are column headings.
- A bare space is ambiguous for the same reason as `-`: nothing stops a description or a
  population name containing one.

The old form was `A1 F30000 I1-1`, which solved the same problem with a letter tag on each
part. The tags were doing the separating, and they named things -- ALE, flask, isolate --
that the schema no longer has.
"""

import re

#: What goes between the parts. Change it here; `format_coordinate` is the only writer, and
#: `RETIRED_LABEL` below is what recognises the shape this replaced.
SEPARATOR = " / "


def format_coordinate(population, time_point, sample):
    """`Ara-1 / 500 / 763A`.

    Every part is stringified rather than formatted: two of the three have been text since
    `aledb_experiment.0008`, and the third is an integer that has nothing to gain from `%d`.
    """
    return SEPARATOR.join("" if part is None else str(part)
                          for part in (population, time_point, sample))


#: A label of the shape this replaced: `A1 F30000 I1` or `A1 F30000 I1 R1`.
#:
#: Only ever used to *recognise* one, by `./aledb relabel_samples`. No import has written a
#: description of this shape -- `gd_import` passes `""` for an A-F-I-R filename precisely so
#: that the computed coordinate shows instead -- so anything matching it was typed by hand
#: when the coordinate looked like that, and is now a label that contradicts the one beside
#: it. Anchored, because a description that merely *contains* such a string is prose.
RETIRED_LABEL = re.compile(r"^A\S+ F\S+ I\S+( R\S+)?$")

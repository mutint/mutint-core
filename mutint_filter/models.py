"""No models.

`AleExperimentFilter` stood here: one row per experiment holding `min_cutoff`, `max_cutoff` and
`ignored_genes`, edited at `/filter` by anyone with write access on the experiment. **It was
shared.** Changing your own view changed everybody's, silently, with no record of who did it --
which conflated curating a dataset, `mutint_curate`'s job and logged and reversible, with
choosing what you personally want to look at, which is nobody else's business.

Filtering is a value a reader carries now, in their session, applied by the page that reads it.
See `mutint_filter/view_filter.py`. There is nothing left to store, and so nothing to default, to
keep fresh, or to send anyone to a page to edit -- the `experiment_filter` rebuilder and the
`Filter` nav entry went with the table.

This model had already been shedding parts for the same reason the whole of it now goes: its
three mutation-id hide-lists (`0003`) were a delete that kept the row, its two `frequency_gatk`
cutoffs (`0004`) were compared against a column no import path ever wrote, and `GlobalFilter`
(`0005`) was a second, installation-wide copy of the same idea. `0006` drops what is left.
"""

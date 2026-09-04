# Showing filtered data

**A filter belongs to whoever is reading.** It is not a property of the experiment, it is not
stored in any table of ours, and it is not the same for two people looking at the same page. It
lives in the reader's session, dies when their browser closes, and shapes both what your page
shows and what it downloads.

There was a shared `AleExperimentFilter` row per experiment until recently, edited at `/filter`
by anyone with write access — so changing your own view changed everybody's. That conflated two
different things: **curating** a dataset, which is `aledb_mutation_editor`'s job and is logged
and reversible, and **choosing what you want to look at**, which is nobody else's business.

## Getting it

```python
from aledb_filter.util import filter_observed_mutations
from aledb_filter.view_filter import get_view_filter

def my_page(request):
    experiment = aledb_seq.views.common.get_ale_experiment(request)
    view_filter = get_view_filter(request, experiment.ale_id)

    rows = filter_observed_mutations(my_queryset, view_filter=view_filter)
```

`get_view_filter` never returns `None` — an unfiltered reader gets `ViewFilter.EMPTY`, and
passing that is the same as passing nothing. Everything after the queryset is **keyword-only**,
deliberately: two call sites used to pass an experiment id positionally into the second slot, and
this repo has a scar from a `request` landing in a `sample_type` parameter and silently dropping
every population sample from two pages.

If you want counts rather than rows, use `filtered_observed_mutation_queryset`, which returns
`(queryset, ignored_genes)` — the frequency cutoff applied in SQL, plus the genes you still have
to test per row, because "every gene this mutation touches is on the ignore list" is a set-subset
test over a parsed column and there is no SQL for it. `gene_is_filtered(gene, ignored_genes)` is
that test; do not write a second one.

## Showing the controls

```django
{% load view_filter %}
{% view_filter_form %}
{% view_filter_summary %}
```

`{% view_filter_form %}` is the inputs wrapped in their own GET form. If your page already has a
form carrying its view state — the shared `base_table_template.html` keeps columns, ALE, sample
type and tags in one form behind one Apply button — use `{% view_filter_fields %}` instead and put
them inside it. Two Apply buttons on one page means each discards the other's pending edits.

**If you render `base_table_template.html` you already have both.** That is what the tags are tags
for: Compare, Fixed Mutations and Converged Mutations got their controls without an edit to any of
their repositories.

Both render **nothing** when the context has no `ale_experiment_id`. That is deliberate and it is
the rule to keep: a control that does nothing is worse than no control. There used to be a
`show_filter_toggles` flag each page had to set for exactly this reason, and it was forgotten on
three pages, which rendered a dead checkbox for a year.

## Ancestral mutations, which are not a filter

An experiment may designate one sample as its **ancestor**. Its mutations were there before the
first flask, so they are subtracted from every other sample before anything is computed, and the
sample itself leaves every listing.

**This is the opposite of everything above.** The reader's filter is theirs, lives in their
session, dies with their browser and is one click from cleared. This belongs to the dataset, is
the same for everyone, and **there is no opting out** — no toggle, no query parameter, no
`ancestor=None` to pass. If you are deriving something, you subtract it.

```python
from aledb_seq.util import observations_for_samples

queryset = observations_for_samples(list(reseq_dict), experiment_id)
queryset, ignored_genes = filtered_observed_mutation_queryset(queryset, view_filter=view_filter)
```

That is the same two lines you already write, with the first one changed. Do not write
`ObservedMutation.objects.filter(sample_id__in=...)` by hand — four repos did,
which is why this helper exists.

**Dropping the ancestor from your sample list is not enough**, and this is the mistake to avoid
because it looks almost right. `get_reseq_ordered_dict` already excludes the ancestor, so its
column disappears from your table and the page looks correct — while its mutations sit in every
other sample. An ancestral mutation is present in every ALE by construction, so convergence
reports every one of them as convergent and fixation reports every one as fixed. The subtraction
has to reach the derivation, exactly as the section below says the reader's filter does.

If your page curates rather than reads — it edits or deletes samples — pass
`get_reseq_ordered_dict(experiment_id, include_ancestor=True)`. Nothing else should.

For a queryset spanning experiments, `aledb_experiment.ancestor.exclude_all_ancestry(queryset)`
takes no experiment id. It is unambiguous because `Mutation` rows are per experiment, so an id
observed in one experiment's ancestor cannot turn up in another's samples.

`{% view_filter_summary %}` names the ancestor for you. A page passing `own_rules=` still gets
that sentence — `own_rules` says you have a different *frequency* rule, not that you skipped the
subtraction. The one page that genuinely does not subtract passes
`{% view_filter_summary ancestor_subtracted=False %}`, and it is the per-sample breseq table,
which tints those rows red instead of hiding them.

## Filter *before* you analyse, not after

If your plugin derives something — what has fixated, what has converged — **the filter has to
reach the derivation.** Fixation asks what is present in both of an ALE's last two flasks, so
hiding a low-frequency call in the last flask changes the answer. Convergence asks which genes
were hit in more than one ALE, so an ignored gene must not make anything else look convergent.
Filtering the *result* would show a different set of rows for the same claim.

So resolve once in the view, pass it down into the computation, and pass `view_filter=None` to
`get_table_body` — those rows have already been through it.

## Downloads

An export handler may take the reader's filter:

```python
def my_export(experiment_id, view_filter=None):
    ...

register_export_handler('my_mut', my_export, label='My Mutations')
```

The second argument is optional and the registry inspects your signature once, so a handler
written before this existed keeps working untouched. Take it if the download should match the
page it was launched from — which is usually the point.

## When *not* to filter

Three cases, and each is a decision rather than an oversight:

- **An inventory of the installation.** `aledb_dashboard` counts what the deployment holds. A
  site-wide total computed through one person's cutoff answers a question nobody asked, and once
  filtering is per-reader it stops being computable at all — a shared table cannot be keyed by
  user. `/stats` is the same argument at experiment scale.
- **A page that must show what is stored.** `aledb_mutation_editor` is deliberately unfiltered: a
  mutation hidden from every table still has to be reachable somewhere it can be removed, or it
  cannot be curated and comes back the moment somebody widens their filter.
- **A page with rules of its own.** `aledb-phylogeny` encodes frequency in three states rather
  than excluding on it. It renders no controls, and says so with
  `{% view_filter_summary own_rules="..." %}` — because an empty summary reads as "no filtering
  here" when the truth is "different filtering here".

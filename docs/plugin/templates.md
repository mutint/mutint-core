# Templates and static files

## Namespacing

Both directories are searched flat across every installed app, so a template at
`templates/index.html` in your plugin competes with every other app's. Namespace by app:

```
mutint_yourthing/templates/yourthing/tree.html      ->  render(..., "yourthing/tree.html")
mutint_yourthing/static/mutint_yourthing/thing.css   ->  {% static 'mutint_yourthing/thing.css' %}
```

## Extending core's base template

```django
{% extends 'base.html' %}

{% block title %}{{ title }}{% endblock %}
{% block scripts_and_style %}
    <link rel="stylesheet" href="{% static 'mutint_yourthing/thing.css' %}">
{% endblock %}
{% block header %}<b>{{ project_name }}: {{ experiment_name }}</b> - Your Thing{% endblock %}

{% block content %}
    ...
{% endblock %}
```

`base.html` draws the sidebar from `nav_registry`, so your entry appears without the template
knowing anything about it.

!!! warning "Content outside a block is silently discarded"

    A `<script>` appended after `{% endblock %}` never renders and no error is raised. If
    something you added is simply not on the page, check that it is inside a block before
    looking anywhere else.

## Reusing core's mutation table

A plugin that shows mutations should render them the way every other page does, not imitate
it. The cells come from `mutint_import.annotate.display`, a port of the code that wrote the
report the sample was imported from, and they mean nothing without the surrounding columns.

- **`mutint_sample/templates/breseq_table/_mutation_table.html`** — one sample's mutations, the
  partial the per-sample page and the genome browser share.
- **The mutation matrix** — mutations down, samples across, drawn with the same cells.
  `mutint-compare` and core's Search are both this one component with a different queryset;
  Compare also hands it *row sets*, below.

### The mutation matrix

Build it from a list of `MutationCall`s and the samples that should be columns:

```python
from mutint_sample.mutation_matrix import build_matrix
from mutint_sample.util import get_ordered_sample_dict

sample_dict = get_ordered_sample_dict(experiment.id, population, sample_type)
matrix = build_matrix(calls, sample_dict, experiment=experiment,
                      csv_title="%s_ExpID%d" % (experiment.name, experiment.id))
```

`sample_dict` is `{sample_id: Sample}` in column order — `get_ordered_sample_dict` for one
experiment's samples (the designated ancestor already left out), `samples_in_calls(calls)`
for the samples that appear in a set of calls. Pass `labels="qualified"` on a page that spans
experiments so each column names its experiment. A row is one mutation and appears when at least
one listed sample carries it; each sample cell is that sample's frequency, linked into the genome
browser when the sample has reads. The `browse_url(call)` and `refseq_url(mutation)` callables
can be replaced if your page links elsewhere.

Then either render **`mutation_matrix/page.html`** — Compare's page, where every control
works in the browser — or put the tag in a page of your own:

```django
{% load mutation_matrix %}
{% mutation_matrix matrix empty_message="No mutations matched." %}
```

A page rendering the tag links three assets, and a core test checks that they travel together:
`css/breseq_table.css`, `js/breseq_table.js` and `js/mutation_matrix.js`. DataTables and
`mutint_select_list.js` come from `base.html`.

The tag alone draws six tabs above the table -- Samples, Mutations (the mutation types),
References, Columns (descriptive columns and cell padding), Frequencies (how a cell shows
its frequency), Sets when the matrix has row sets, and Export -- from `control_tabs.html`. `mutation_matrix/page.html` renders its own strip and passes
`controls=False`; see below for what it adds. The menus let the reader show and
hide descriptive columns, samples, mutation types and reference sequences. Those choices are remembered for a signed-in reader through
`mutint_common.preferences` — keys `mutation_matrix.columns`, `mutation_matrix.types`,
`mutation_matrix.frequency` (how a cell shows its frequency: number, bars, heat map, or both),
`mutation_matrix.page_length` (Show N entries, All included),
`mutation_matrix.view` (Normal or Condensed cell padding) and `mutation_matrix.show` (which row
set, below) (everywhere), and `mutation_matrix.samples.<experiment_id>` and
`mutation_matrix.references.<experiment_id>` (shared by every matrix page of that experiment,
the second by the per-sample Mutations page as well) — and in the browser's localStorage
otherwise. The store's client half is `js/mutint_preferences.js`, loaded from `base.html`, and
a page of your own may call `window.mutintPreferences(...)` the same way. The Export tab
holds Export CSV, a menu of two — the rows showing, after every menu and the search box, or
every row the server produced; visible columns only, either way — and Export SVG.

**Row sets** are how a page offers a subset of its rows without being a second page. Hand
`build_matrix` `sets=(RowSet("fixed", "Fixed", mutation_ids), ...)`: each row is annotated
with the keys of the sets holding its mutation, and a **Show** menu — All, then one entry per
set with its count — appears above the table, remembered like the other menus. The matrix
does not filter and asks nothing about what a set means; how the ids were chosen is your
page's business, and a page with no sets to offer gets no menu.

A set can instead be decided **in the browser**, so it follows what the reader shows:
`client_sets=(ClientSet("convergent", "Convergent"), ...)` puts the entry in the menu with no
ids, and your page's script registers the rule,
`window.mutintMatrixSets.register("convergent", function (state, params) {...})`. It is
called on every change with `state.samples` (the shown sample columns: `index`,
`population`, `time`) and `state.rows` (`id`, `genes`, and `cells` by sample index, null
where nothing is shown), plus `params`, the values of your `[data-set-param]` inputs, and
returns `{ids: [...], note: "...", error: "..."}`. Compare's Convergent and Fixed are the
worked example (`mutint_compare/static/mutint_compare/compare_sets.js`).

**Ancestral rows are tinted, not filtered.** Pass `build_matrix(...,
ancestral_mutation_ids=ancestral_mutation_ids(experiment_id))` and each row gains
`ancestral: true|false`, which the script turns into the per-sample table's red. Such a row
is in no row set, so the Show menu drops it.

**`mutation_matrix/page.html` decides everything in the browser.** Its view sends every
sample and every call -- `get_all_calls_filtered(experiment_id, include_ancestral=True)`,
with no view filter -- and the page's tabs are Treatments and Populations (each with a
"Color sample columns by" box), Samples (with Sample types), Time Points, and the tag's own
with Sets; its Mutations tab also carries the Show/Hide ancestral mutations button, and its
Frequencies tab the
reader's frequency range, written back to their session through `/filter/set`. The
ignored-genes box is not offered for now, and the page applies no gene list without it. It wants `experiment_id, experiment_name, project_name, project_id,
template_header, title, matrix, empty_message`, plus `view_filter_state` (the reader's filter
as `mutint_filter.views.filter_json` gives it), `ancestor` (`describe_ancestor`) and
`ancestral_shown`. It has two blocks, both on the Sets tab: `matrix_controls`, beside the
Show menu, for inputs your sets read (`data-set-param`); and `matrix_summary`, under the
sets' sentences, which the script writes. Compare is the page that uses it. The table scrolls inside its own box
with the header and the descriptive columns pinned, in the order `build_matrix` produced the
rows — nothing sorts — and each sample's header links to that sample's Mutations page and is
colored by population (`SampleColumn.palette`), so an experiment's ALEs read as bands.
A plugin that wants to remember something of its own writes under its own prefix through the same
`/preferences/` endpoint; nothing in core needs to know.

The filter reaches the *derivation*, not the matrix: build your queryset through the reader's
filter (see [filtering](filtering.md)) and hand the result over. `build_matrix` filters nothing.

## Overriding a core template

An assembled project's `templates/` directory is searched ahead of every app's, so a
deployment can replace any core template by putting a file at the same path. That mechanism is
for deployments, not plugins: a plugin shipping a file at a core template's path would
silently change pages that have nothing to do with it, and which of the two wins would depend
on `INSTALLED_APPS` order.

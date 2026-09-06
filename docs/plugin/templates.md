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
{% block header %}<b>{{ ale_project_name }}: {{ experiment_name }}</b> - Your Thing{% endblock %}

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
  `mutint-compare`, `mutint-fixation`, `mutint-converge` and core's Search are all this one
  component with a different queryset.

### The mutation matrix

Build it from a list of `MutationCall`s and the samples that should be columns:

```python
from mutint_sample.mutation_matrix import build_matrix
from mutint_sample.util import get_reseq_ordered_dict

reseq_dict = get_reseq_ordered_dict(experiment.id, population, sample_type)
matrix = build_matrix(calls, reseq_dict, experiment=experiment,
                      csv_title="%s_ExpID%d" % (experiment.name, experiment.id))
```

`reseq_dict` is `{sample_id: Sample}` in column order — `get_reseq_ordered_dict` for one
experiment's samples (the designated ancestor already left out), `get_ordered_reseq_dict(calls)`
for the samples that appear in a set of calls. Pass `labels="qualified"` on a page that spans
experiments so each column names its experiment. A row is one mutation and appears when at least
one listed sample carries it; each sample cell is that sample's frequency, linked into the genome
browser when the sample has reads. The `browse_url(call)` and `refseq_url(mutation)` callables
can be replaced if your page links elsewhere.

Then either render **`mutation_matrix/page.html`** — the page the three plugin tables share:
the ALE and sample-type pickers, the reader's filter controls and summary, and the matrix — with
`experiment_id, ales, population, sample_type, experiment_name, ale_project_name, ale_project_id,
template_header, title, matrix, empty_message` in the context; or put the tag in a page of your
own:

```django
{% load mutation_matrix %}
{% mutation_matrix matrix empty_message="No mutations matched." %}
```

A page rendering the tag links three assets, and a core test checks that they travel together:
`css/breseq_table.css`, `js/breseq_table.js` and `js/mutation_matrix.js`. DataTables and
`mutint_select_list.js` come from `base.html`.

Three menus above the table let the reader show and hide descriptive columns, samples and
mutation types. Those choices are remembered for a signed-in reader through
`mutint_common.preferences` — keys `mutation_matrix.columns`, `mutation_matrix.types` and
`mutation_matrix.frequency` (how a cell shows its frequency: number, bars, heat map, or both)
(everywhere) and `mutation_matrix.samples.<experiment_id>` (shared by every matrix page of that
experiment) — and in the browser's localStorage otherwise. The table scrolls inside its own box
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

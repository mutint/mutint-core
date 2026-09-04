# Templates and static files

## Namespacing

Both directories are searched flat across every installed app, so a template at
`templates/index.html` in your plugin competes with every other app's. Namespace by app:

```
aledb_yourthing/templates/yourthing/tree.html      ->  render(..., "yourthing/tree.html")
aledb_yourthing/static/aledb_yourthing/thing.css   ->  {% static 'aledb_yourthing/thing.css' %}
```

## Extending core's base template

```django
{% extends 'base.html' %}

{% block title %}{{ title }}{% endblock %}
{% block scripts_and_style %}
    <link rel="stylesheet" href="{% static 'aledb_yourthing/thing.css' %}">
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
it. The cells come from `aledb_import.annotate.display`, a port of the code that wrote the
report the sample was imported from, and they mean nothing without the surrounding columns.

- **`aledb_seq/templates/breseq_table/_mutation_table.html`** — one sample's mutations, the
  partial the per-sample page and the genome browser share.
- **`mutation_table_builder` + `base_table_template.html`** — the cross-sample table, mutations
  down and samples across. `aledb-compare`, `aledb-fixation` and `aledb-converge` are all this
  same view with a different queryset.

If you use the cross-sample table, take the column index from the constant rather than
counting:

```python
from aledb_common.constants import REFSEQ_COLUMN_IN_MUT_TABLE
```

Removing one column from that table once shifted every other column left by one. Everything
in `table_template.js` is expressed relative to that constant, and the plugins that import it
followed for free; one that had hardcoded an index would have broken silently, rendering a
table labelled one way and sorted another.

## `table_template.js` is a Django template

It is included inside a `<script>` tag, which is what lets its three endpoint URLs be reversed
by name rather than written out.

!!! danger "A `//` comment is not a template comment"

    The template engine does not recognise JavaScript comments. Anything tag-shaped inside
    one is still parsed and executed — writing a tag name in a `//` line runs it. The file
    describes its tags in prose rather than spelling them out, for exactly this reason.

## Overriding a core template

An assembled project's `templates/` directory is searched ahead of every app's, so a
deployment can replace any core template by putting a file at the same path. That mechanism is
for deployments, not plugins: a plugin shipping a file at a core template's path would
silently change pages that have nothing to do with it, and which of the two wins would depend
on `INSTALLED_APPS` order.

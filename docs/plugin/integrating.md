# Fitting into core's pages

A plugin does not have to add a page of its own. Five registrations let it appear inside
pages core already owns.

## An export type

Core's export menus are rendered from the registry, so a new type appears in them without an
edit:

```python
from aledb_common.plugin_registry import register_export_handler

register_export_handler('yourthing_mut', get_your_calls,
                        label='Your Mutations')
```

The handler takes an experiment id and returns a queryset of `MutationCall`. `label` is
what the menu shows; without it the menu shows the type string, which is a name for a URL
rather than for a person.

It may also take the reader's filter, so a download matches the page it was launched from:

```python
def get_your_calls(experiment_id, view_filter=None):
    ...
```

The second argument is optional. The registry inspects your signature once at registration and
hands the filter only to a handler that can take it, so one written before this existed keeps
working untouched. See [Showing filtered data](filtering.md).

`aledb_export/util.py` dispatches on the type: `mut` uses the base queryset and everything
else is looked up here. There are no plugin names anywhere in core's export code.

If your handler reads derived data, this is a read path like any other — call `ensure_fresh`
inside it, or an export will happily write out last week's answer. `aledb-fixation` registers
the same function as both its page's queryset and its export handler, which is the reason
the freshness check lives in the function rather than in the view.

## An import type

```python
from aledb_common.import_registry import register_import_handler

register_import_handler(
    name='yourthing',
    label='Your measurements (.tsv)',
    patterns=['.tsv'],
    handle=import_your_files,
    priority=50)
```

It appears in the Add Data page's type dropdown and in its auto-detection.

`priority` is real ordering, not a preference: a reference genome must be established before
anything hash-checked against it, so core registers `reference` at 10, `breseq_folder` at 50
and `genomediff` at 60. Pick a number that puts you after whatever your files depend on.

`patterns` does more than route. `identify()` uses it to tell somebody who chose the wrong
type what their file looks like instead, and the Add Data page serialises it to say the same
thing *before* anything uploads. Both come free from registering.

Two optional declarations worth knowing:

- `requires_reference=True` hides your type until the experiment has a reference genome. It is
  hidden rather than greyed out, because a dropdown entry you can see and cannot pick is a
  dead end; the page's banner explains the short menu instead.
- `only_without_reference=True` is the opposite, for a type that establishes one.

If your shape is not a suffix match — a directory, say — supply your own `detect`.

`menu_order` is where you sit in the dropdown, and it defaults to `priority` so you need not
pass it. The two are separate because `priority` is correctness and cannot be moved: core puts
`.gd` first and `replace_annotation` last, which no arrangement of run order could express.

### Reporting progress

An import that takes minutes should say how far it has got, or the page looks hung. Nothing is
required of you — a handler that reports nothing still returns its rows, and they still appear
— but two lines make your type behave like core's:

```python
from aledb_common import import_progress

for path in paths:
    import_progress.begin(name_of(path))
    ...
    import_progress.report({'file': name_of(path), 'mutations': n, 'error': None})
```

Both are no-ops unless something is listening, so the CLI and `load_example` are unaffected.

**The one rule: the name you announce must be the `file` key you report.** Units are paired by
position, so a mismatch renders a row that never fills in. If one unit of your work is not one
claimed file — a breseq sample is a directory of five — declare `list_units=` to say what your
units are called, mirroring exactly what your own loop will report:

```python
register_import_handler(
    ...,
    list_units=lambda staged_root, claimed: [os.path.basename(p) for p in claimed])
```

It defaults to `claimed` itself, which is right whenever one file is one unit.

## A section on `/about`

```python
register_about_section(self, name='aledb-yourthing',
                       template='about/sections/aledb_yourthing.html')
```

Called with `self`, the `AppConfig`, because the unit is the **component** — the checkout an
app came from — rather than the Django app. aledb-core is fifteen apps and has to read as one
entry.

Every installed component appears whether or not it registers anything, with its directory
name and its git revision, so the page is an inventory as much as prose. Registering adds your
words under your heading. A template that does not exist drops to a bare heading with a logged
warning rather than breaking a page that is mostly other components' content.

## A panel on the experiment Overview

```python
from aledb_common.panel_registry import register_overview_panel

register_overview_panel(self, name='needle_plot',
                        title='Mutation Needle Plot',
                        template='needle/panel.html',
                        context=needle_panel_context)
```

`/stats` draws a heading and your rendered template under the counts and the sample table it
owns itself. Your `context` callable takes `(experiment, request)` and returns a dict — the
request as well as the experiment, because a panel may legitimately depend on the query string:
the needle plot's sequence picker is a `?contig=` away.

**Your template is the body only.** The heading and the rule above it are drawn by the page, so
two components cannot disagree about what a section there looks like.

Each panel is rendered on its own, with its own context, so two panels using the name `data`
for different things cannot read each other's. A panel whose callable or template raises is
dropped with a logged warning and the rest of the page renders — the same posture as a nav
entry whose route will not reverse.

This is the seam to reach for when what you have is *one panel and not a page*.
[`aledb-needle`](https://github.com/barricklab) — the mutation needle plot — is a component
that registers a panel and an About section and nothing else whatever: no URL, no nav entry, no
model, no migration. Before this registry existed it had to live in aledb-core, for no better
reason than that `/stats` is where it is drawn.

## Context for the experiment views

```python
from aledb_common.context_registry import register_experiment_context_provider

register_experiment_context_provider(add_your_context)
```

Your callable contributes to the context of core's experiment views, which is how
`aledb_bibliome` puts publication data on those pages without core depending on it. Use it
when your data belongs *on somebody else's page* and that page already renders it; reach for
`register_overview_panel` above when you are bringing the markup too, and use a nav entry and
your own view when it deserves its own page.

## When contigs are renamed

```python
from aledb_common.plugin_registry import register_sequence_rename_hook

register_sequence_rename_hook(resync_after_rename)
```

Called as `fn(experiment_id, renames)` where `renames` maps old contig name to new, when
an experiment's reference contigs are renamed. It is separate from the rebuild registry
because a rename carries a *mapping*: rewriting a stored blob in place is as valid a response
as recomputing from scratch, and only you know which applies.

This matters more than it sounds if you store anything positional. `aledb-phylogeny` rebuilds
its whole tree here, because its character matrix is ordered by `reseq_reference` and its
site list is positional — renaming one contig can move its columns relative to another's and
leave every stored index off by some amount. A single-contig experiment is unaffected, which
is exactly what makes it the kind of bug that ships.

Failures are isolated per hook, so yours raising cannot undo a rename that is already true.

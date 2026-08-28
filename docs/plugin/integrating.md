# Fitting into core's pages

A plugin does not have to add a page of its own. Five registrations let it appear inside
pages core already owns.

## An export type

Core's export menus are rendered from the registry, so a new type appears in them without an
edit:

```python
from aledb_common.plugin_registry import register_export_handler

register_export_handler('yourthing_mut', get_your_observations,
                        label='Your Mutations')
```

The handler takes an experiment id and returns a queryset of `ObservedMutation`. `label` is
what the menu shows; without it the menu shows the type string, which is a name for a URL
rather than for a person.

It may also take the reader's filter, so a download matches the page it was launched from:

```python
def get_your_observations(experiment_id, view_filter=None):
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

## Context for the experiment views

```python
from aledb_common.context_registry import register_experiment_context_provider

register_experiment_context_provider(add_your_context)
```

Your callable contributes to the context of core's experiment views, which is how
`aledb_bibliome` puts publication data on those pages without core depending on it. Use it
when your data belongs *on somebody else's page*; use a nav entry and your own view when it
deserves its own.

## When contigs are renamed

```python
from aledb_common.plugin_registry import register_sequence_rename_hook

register_sequence_rename_hook(resync_after_rename)
```

Called as `fn(ale_experiment_id, renames)` where `renames` maps old contig name to new, when
an experiment's reference contigs are renamed. It is separate from the rebuild registry
because a rename carries a *mapping*: rewriting a stored blob in place is as valid a response
as recomputing from scratch, and only you know which applies.

This matters more than it sounds if you store anything positional. `aledb-phylogeny` rebuilds
its whole tree here, because its character matrix is ordered by `reseq_reference` and its
site list is positional — renaming one contig can move its columns relative to another's and
leave every stored index off by some amount. A single-contig experiment is unaffected, which
is exactly what makes it the kind of bug that ships.

Failures are isolated per hook, so yours raising cannot undo a rename that is already true.

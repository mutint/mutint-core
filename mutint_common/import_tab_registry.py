"""Registry of the tabs on the Import data page.

The page is one strip of tabs, and each tab is a way of getting data into an experiment.
Core registers one tab per import type it ships -- Reference Sequence, Genome Diff, Variant
Call Format, breseq output, Replace Annotation -- and each of those is the Import page itself
with that type chosen. A plugin registers a tab too, and a plugin's tab may be **a page of its
own**: mutint-breseq's Run breseq tab is that plugin's launcher, which needs a sample name and
a command line that no import handler can carry, and which renders the same strip so it reads
as one more way in rather than a page somewhere else.

So a tab is one of two things, and the registry keeps them apart by which argument it was
given: `import_types=` names handlers in `import_registry`, in preference order, and lands on
the Import page as the first of them the experiment can run -- Reference Sequence is
`reference` until the experiment has one and `replace_annotation` after, one tab for one
question; `url_name=` (or a literal `url=`) names a page the tab links to, with
`?experiment_id=` appended the way sidebar entries in the experiment section get it. A tab
whose `url_name` will not reverse is skipped, the posture `nav_registry` takes, so a
half-installed plugin cannot leave a dead tab.

**This is the ninth registry, and it is separate from `import_registry` on purpose.** That one
is about bytes: which files a type claims and what runs on them, and it is consulted at
finalize with no page in sight. This one is about the page: what the strip offers and in
what order, which is `INSTALLED_APPS` order, the rule every other registry of things-drawn
follows. Core's tabs are registered from `mutint_import`'s `AppConfig.ready()`, after its
handlers, so core is a caller of this registry like any plugin.
"""

_tabs = []


def register_import_tab(key, label, *, import_type=None, import_types=None, url_name=None,
                        url=None, requires_reference=False, last=False):
    """Register a tab on the Import data page (from `AppConfig.ready()`).

    key           identifies the tab; the page marks the matching one active, and the tab's
                  URL names it (`?tab=<key>`)
    label         the words on the tab
    import_types  `import_registry` handler names, in preference order: the page imports as
                  the first one the experiment can run now, and when none can, says why the
                  first cannot. `import_type=` is the one-name form. Mutually exclusive
                  with the two below.
    url_name      a URL pattern name, reversed at render time, for a tab that is a page of
                  its own; `?experiment_id=` is appended
    url           a literal path, for the same, when reversing is not wanted

    requires_reference
                  for a `url_name`/`url` tab only: hide it until the experiment has a
                  reference. A tab naming an import type needs no such flag -- the registry
                  asks `import_registry` whether the type can run -- but a tab that is a page
                  of its own is opaque, so the plugin says. mutint-breseq's Run breseq is one:
                  breseq calls mutations *against* a reference and its launcher refuses
                  without one.
    last          sort after every other tab, plugins' included. There is still no `order=`:
                  the strip's order is INSTALLED_APPS order, and this says only "the tail",
                  which INSTALLED_APPS cannot express at all because plugins are appended
                  after every core app. Update Annotation is there because it is a correction
                  to an experiment that is already set up, not a way of starting one.

    Tabs otherwise render in registration order: apps in INSTALLED_APPS order, and within an
    app in the order this is called.
    """
    if import_type is not None:
        if import_types is not None:
            raise ValueError("register_import_tab() takes import_type or import_types, not both")
        import_types = (import_type,)
    if sum(x is not None for x in (import_types, url_name, url)) != 1:
        raise ValueError("register_import_tab() needs exactly one of import_types, url_name, url")
    if import_types is not None and not import_types:
        raise ValueError("register_import_tab() needs at least one import type")
    _tabs[:] = [t for t in _tabs if t["key"] != key]
    _tabs.append({"key": key, "label": label,
                  "import_types": tuple(import_types) if import_types else None,
                  "url_name": url_name, "url": url,
                  "requires_reference": requires_reference, "last": last})


def unregister_import_tab(key):
    _tabs[:] = [t for t in _tabs if t["key"] != key]


def offered_with_reference(offered):
    """Whether the experiment these types were computed for has a reference.

    Read off the offered set rather than asked again: `replace_annotation` is offered exactly
    when there is one, so the answer is already in hand and costs no second query.
    """
    return "replace_annotation" in offered


def _offered_names(experiment_id):
    """The import types this experiment can run right now, by name.

    Asked here so every caller of `get_import_tabs` -- the page and the template tag on any
    plugin's page -- gets the same strip from the same argument, rather than each having to
    work out whether there is a reference and pass it in.
    """
    from mutint_experiment.models import Experiment
    from mutint_common.import_registry import get_import_types_for
    from mutint_import.reference_store import has_reference

    experiment = Experiment.objects.filter(pk=experiment_id).first()
    # An id naming no experiment answers as one with no reference rather than as one that can
    # import nothing. The page 404s on such an id long before it draws a strip, so the only
    # caller this reaches is a test or a stale link -- and "every tab is hidden" is a
    # confusing way to say "no such experiment".
    return {entry["name"]
            for entry in get_import_types_for(
                has_reference(experiment) if experiment is not None else False)}


def get_import_tabs(experiment_id):
    """`[{'key', 'label', 'url', 'import_types'}, ...]` for one experiment, in order.

    URLs are reversed here rather than at registration: the URLconf is not loaded while
    `ready()` runs. A tab whose route is not installed is skipped rather than raising.

    **A tab that cannot run now is not shown.** It used to stay on the strip with a sentence
    saying what it was waiting for, on the reasoning that a tab telling you to get a reference
    in first teaches somebody the order to do things in. What it actually produced was a strip
    of mostly-dead tabs on a new experiment, each of which errors when opened. So the strip is
    now the ways in that work: with no reference that is Reference Sequence **and Results
    Folder** -- which needs none, because a breseq folder brings its own, and is the other way
    to start an experiment from nothing.

    `last` tabs are moved to the end, stably, so everything else keeps INSTALLED_APPS order.
    """
    from django.urls import NoReverseMatch, reverse

    offered = _offered_names(experiment_id)
    tabs = []
    for tab in sorted(_tabs, key=lambda entry: 1 if entry.get("last") else 0):
        if tab["import_types"] is not None:
            if not any(name in offered for name in tab["import_types"]):
                continue
        elif tab.get("requires_reference") and not offered_with_reference(offered):
            continue
        try:
            if tab["import_types"] is not None:
                url = "%s?experiment_id=%s&tab=%s" % (
                    reverse("import_data"), experiment_id, tab["key"])
            elif tab["url"] is not None:
                url = "%s?experiment_id=%s" % (tab["url"], experiment_id)
            else:
                url = "%s?experiment_id=%s" % (reverse(tab["url_name"]), experiment_id)
        except NoReverseMatch:
            continue
        tabs.append({"key": tab["key"], "label": tab["label"], "url": url,
                     "import_types": list(tab["import_types"] or [])})
    return tabs

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
                        url=None):
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

    Tabs render in registration order: apps in INSTALLED_APPS order, and within an app in
    the order this is called. There is deliberately no ordering parameter.
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
                  "url_name": url_name, "url": url})


def unregister_import_tab(key):
    _tabs[:] = [t for t in _tabs if t["key"] != key]


def get_import_tabs(experiment_id):
    """`[{'key', 'label', 'url', 'import_types'}, ...]` for one experiment, in order.

    URLs are reversed here rather than at registration: the URLconf is not loaded while
    `ready()` runs. A tab whose route is not installed is skipped rather than raising.
    """
    from django.urls import NoReverseMatch, reverse

    tabs = []
    for tab in _tabs:
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

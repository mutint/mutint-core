"""Registry of the tabs on the Curate page.

Curate is one strip of tabs -- Edit, Delete, Add, Copy, History -- each a different moment of
the same task, and until this existed the strip was five `<li>`s written into
`mutint_curate/templates/curate/_toolbar.html`. A plugin with a page that belongs in that strip
(mutint-breseq's Evidence, the evidence breseq could not assign to a mutation, which curating
is what resolves) had nowhere to put it: a sidebar entry would say it is a page somewhere else,
and it is not.

**The fourteenth registry, and `import_tab_registry`'s page-of-its-own form exactly.** A tab
is a `url_name` reversed at render time with `?experiment_id=` appended, a tab whose route
will not reverse is skipped (`nav_registry`'s posture, so a half-installed plugin cannot leave
a dead tab), and order is `INSTALLED_APPS` order -- core's five are registered from
`mutint_curate`'s `AppConfig.ready()`, so core is a caller of this registry like any plugin.
`last=True` is the one ordering word, for History: it sorts after every other tab, plugins'
included, which `INSTALLED_APPS` cannot express because plugins are appended after every
core app.

There is no visibility flag here. Every tab of this strip is about editing an experiment's
mutations, and the Curate sidebar entry already carries `requires_edit`; a plugin's tab
refuses its own writes, as core's do.
"""

_tabs = []


def register_curate_tab(key, label, *, url_name=None, url=None, last=False):
    """Register a tab on the Curate page (from `AppConfig.ready()`).

    key       identifies the tab; a page marks the matching one active with
              `{% curate_tabs key %}`
    label     the words on the tab
    url_name  a URL pattern name, reversed at render time; `?experiment_id=` is appended
    url       a literal path, for the same, when reversing is not wanted -- core's own tabs
              use these because `^$` is the Edit tab and reversing it adds nothing
    last      sort after every other tab, plugins' included
    """
    if sum(x is not None for x in (url_name, url)) != 1:
        raise ValueError("register_curate_tab() needs exactly one of url_name, url")
    _tabs[:] = [t for t in _tabs if t["key"] != key]
    _tabs.append({"key": key, "label": label, "url_name": url_name, "url": url,
                  "last": last})


def unregister_curate_tab(key):
    _tabs[:] = [t for t in _tabs if t["key"] != key]


def get_curate_tabs(experiment_id):
    """`[{'key', 'label', 'url'}, ...]` for one experiment, in order.

    URLs are reversed here rather than at registration: the URLconf is not loaded while
    `ready()` runs. `last` tabs are moved to the end, stably, so everything else keeps
    `INSTALLED_APPS` order.
    """
    from django.urls import NoReverseMatch, reverse

    tabs = []
    for tab in sorted(_tabs, key=lambda entry: 1 if entry.get("last") else 0):
        try:
            path = tab["url"] if tab["url"] is not None else reverse(tab["url_name"])
        except NoReverseMatch:
            continue
        tabs.append({"key": tab["key"], "label": tab["label"],
                     "url": "%s?experiment_id=%s" % (path, experiment_id)})
    return tabs

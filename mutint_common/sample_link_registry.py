"""Registry of links a component adds at the top of a sample's page.

Apps call `register_sample_link()` from their `AppConfig.ready()`; the sample box that heads the
Mutations page and the sample edit page (`sample/_inputs.html`) draws whatever the registered
callables return for that sample. The thirteenth registry.

**Why this exists.** Something a component keeps *about one sample* -- a FastQC report on the
reads it was made from is the first -- had nowhere to be linked from. `panel_registry` is the
experiment's Overview, and `context_registry` hands a view context some core template must
already be written to render. The breseq report's link is written into the page directly
because core keeps that report; a plugin's cannot be.

**Links, not panels.** A sample's page is its mutations, and what a component adds there is a
way to something else rather than content of its own. A callable returns
`[(label, url, title), ...]` -- empty for a sample it has nothing about, which is most of them.

**Failures are isolated**, the posture `panel_registry` takes: a callable that raises is
dropped with a logged warning naming it, and the page renders without its links.

**Ordering is INSTALLED_APPS order**, with no `order` parameter; a link's position is
cosmetic.
"""

import logging

logger = logging.getLogger(__name__)

#: [{'app', 'name', 'links'}], in registration order.
_providers = []


def register_sample_link(app_config, name, links):
    """Register a provider of links for a sample's page (from `AppConfig.ready()`).

    app_config  the AppConfig itself, `self` at the call site.
    name        stable slug, unique per app; registering (app, name) again replaces it.
    links       callable(sample, request) -> [(label, url, title), ...]. `title` is the
                tooltip and may be ''. `request` may be None where a page has none to give.
    """
    key = (app_config.name, name)
    entry = {'app': app_config.name, 'name': name, 'links': links}
    for index, existing in enumerate(_providers):
        if (existing['app'], existing['name']) == key:
            _providers[index] = entry
            return name
    _providers.append(entry)
    return name


def unregister_sample_link(app_label, name):
    """Remove a registered provider. For tests; nothing in the product unregisters."""
    global _providers
    _providers = [p for p in _providers if (p['app'], p['name']) != (app_label, name)]


def sample_links(sample, request=None):
    """Every registered provider's links for `sample`, as [{'label', 'url', 'title'}]."""
    found = []
    for provider in _providers:
        try:
            for label, url, title in provider['links'](sample, request) or ():
                found.append({'label': label, 'url': url, 'title': title or ''})
        except Exception:
            logger.exception("sample links %s.%s failed; skipping them",
                             provider['app'], provider['name'])
    return found

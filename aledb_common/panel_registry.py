"""Registry of panels contributed to an experiment's Overview page.

Apps call `register_overview_panel()` from their `AppConfig.ready()`; `/stats` renders
whatever has been registered, under the counts and the sample table it owns itself. This is
the eighth registry, and the first that lets an app put *its own rendered content* on a core
page rather than contribute a link, a heading, a handler or a name.

**Why this exists.** Every earlier way for a plugin to be seen was a page of its own --
`register_plugin_urlpatterns` plus `register_nav_item`, which is what aledb-compare,
aledb-fixation and aledb-converge are. Something that is one panel and not a page had no seam
at all, so it had to live in core: the needle plot sat in `aledb_stats` beside the Overview's
counts for no better reason than that `/stats` is where it is drawn. `context_registry` gets
context onto an experiment view and stops there -- the template still has to name the block
that renders it, which is exactly the compile-time knowledge of a plugin core is built not to
have.

**A panel is a template plus a callable that builds its context.** Not HTML in a setting, for
the reason `about_registry` gives: a template ships inside the app, renders through the normal
machinery, and a deployment overrides any component's panel by writing a file at the same
path.

**Each panel is rendered on its own.** The page is handed finished HTML per panel rather than
merging every panel's context into one dict and letting `{% include %}` sort it out -- two
panels using the name `data` would otherwise silently read each other's. It is also what makes
the isolation below expressible: there is one place where one panel's failure is caught.

**Failures are isolated.** A panel whose context callable raises, or whose template is missing
or itself raises, is dropped with a logged warning and the rest of the page renders. That is
the posture `nav_registry` takes with a `url_name` that will not reverse and `about_registry`
with a template that is not there: one plugin's typo must not take down a page that is mostly
somebody else's content. The trade is the usual one -- a broken panel is quiet rather than
loud, and the warning names the panel.

**Ordering is INSTALLED_APPS order**, as with `nav_registry` and `about_registry`, and there
is deliberately no `order` parameter. A panel's position on a page is cosmetic; to move one,
move its app in INSTALLED_APPS. (`import_registry` and `rebuild_registry` are the two that do
take an order, and in both it is correctness rather than appearance.)
"""

import logging

logger = logging.getLogger(__name__)

#: [{'app', 'name', 'title', 'template', 'context'}], in registration order.
_panels = []


def register_overview_panel(app_config, name, title, template, context=None):
    """Register a panel on the experiment Overview page (from `AppConfig.ready()`).

    app_config  the AppConfig itself, i.e. `self` at the call site. Kept so a warning about a
                broken panel can name the app it came from, which the panel's own name need
                not resemble.
    name        stable slug, unique per app. Registering the same (app, name) twice replaces
                the first, so a reload cannot double a panel.
    title       the heading the page draws above the panel. The panel's template is the body
                only: the page owns the heading level and the rule above it, so panels from
                different components cannot disagree about what a section looks like.
    template    template name for the body, e.g. 'needle/panel.html'.
    context     optional callable(experiment, request) -> dict, merged into the template's
                context. Both arguments, because a panel may legitimately depend on the query
                string -- the needle plot's sequence picker is a `?contig=` away.

    Returns the name it registered under, as `plugin_registry.register_post_experiment_hook`
    does, so a caller has something to unregister in a test.
    """
    key = (app_config.name, name)
    entry = {'app': app_config.name, 'name': name, 'title': title,
             'template': template, 'context': context}
    for index, existing in enumerate(_panels):
        if (existing['app'], existing['name']) == key:
            _panels[index] = entry
            return name
    _panels.append(entry)
    return name


def unregister_overview_panel(app_label, name):
    """Remove a registered panel. For tests; nothing in the product unregisters."""
    global _panels
    _panels = [p for p in _panels
               if (p['app'], p['name']) != (app_label, name)]


def get_overview_panels():
    """Every registered panel, in registration order. Metadata only, nothing rendered."""
    return list(_panels)


def render_overview_panels(experiment, request):
    """Render every registered panel for `experiment`.

    Returns [{'name', 'title', 'html'}, ...] in registration order, omitting any panel that
    raised. `request` is passed to the renderer, so context processors run and a panel sees
    the same `aledb_version`, user and static configuration the page around it does.
    """
    from django.template.loader import render_to_string

    rendered = []
    for panel in _panels:
        try:
            context = {}
            if panel['context'] is not None:
                context = panel['context'](experiment, request) or {}
            html = render_to_string(panel['template'], context, request=request)
        except Exception:
            # Logged with the app and panel names because the page will simply be missing a
            # section, which is not a symptom anybody can work backwards from.
            logger.exception("overview panel %s.%s failed; skipping it",
                             panel['app'], panel['name'])
            continue
        rendered.append({'name': panel['name'], 'title': panel['title'], 'html': html})
    return rendered

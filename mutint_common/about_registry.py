"""Registry of About-page sections contributed by the installed components.

Apps call register_about_section() from their AppConfig.ready(); mutint_about's page renders
whatever has been registered, plus a heading for every installed component that registered
nothing. This is the fifth registry, alongside the plugin, nav, import and context ones, and
it keeps mutint-core free of any compile-time knowledge of the plugins: a plugin's prose
appears on the About page only when the plugin is installed, and no assembled project has to
be told about it.

**The unit is the component, not the Django app.** mutint-core is fifteen `mutint_*` apps and
has to read as one entry on the page, so entries are keyed by the directory an app package
sits in -- the checkout the app came from. That works out the same in both layouts:

    mutint-core standalone   <repo>/mutint_sample          -> <repo>
    assembled in mutint     mutint/mutint-core/mutint_sample        -> mutint/mutint-core
                            mutint/mutint-fixation/mutint_fixation -> mutint/mutint-fixation
                            mutint/mutint-app/mutint_app       -> mutint/mutint-app

Content is a *template name* rather than a string of HTML or a path to a file. It ships
inside the app, it renders through the normal template machinery, and a deployment overrides
any component's section by writing a file at the same path -- the same seam that lets it
replace any other core template (see "Branding and deployment identity" in CLAUDE.md).
"""

import logging
import os

logger = logging.getLogger(__name__)

# Component directory -> {'name', 'version', 'template'}. One app per component registers;
# a second call for the same component replaces the first.
_sections = {}


def component_dir(app_config):
    """The directory of the component `app_config` belongs to."""
    return os.path.dirname(os.path.abspath(app_config.path))


def register_about_section(app_config, name=None, version=None, template=None):
    """Register a component's About-page section (called from AppConfig.ready()).

    Sections render in INSTALLED_APPS order -- the order each component's first app appears
    there. There is deliberately no ordering parameter, as with nav_registry: to move a
    section, move its app in INSTALLED_APPS.

    app_config  the AppConfig itself, i.e. `self` at the call site. The entry is keyed by the
                component the app belongs to, and this is what identifies it.
    name        heading, e.g. 'mutint-core'. Defaults to the component directory's name.
    version     version string, shown beside the name. Omit it and only the git revision
                shows -- most components have no version of their own.
    template    template name whose content becomes the section's body, e.g.
                'about/sections/mutint_core.html'. Omit it for a heading alone.
    """
    _sections[component_dir(app_config)] = {
        'name': name,
        'version': version,
        'template': template,
    }


def get_about_sections():
    """One entry per installed component, in INSTALLED_APPS order.

    Returns [{'name', 'version', 'template', 'revision', 'revision_url', 'anchor'}, ...].

    A component that registered nothing still appears, with its directory's name and whatever
    revision git can tell us: the page is an inventory of what is running as much as it is
    prose about it.
    """
    from django.apps import apps
    from django.utils.text import slugify

    from mutint_common.util import get_revision

    sections = []
    seen = set()
    for app_config in apps.get_app_configs():
        if not _is_first_party(app_config):
            continue
        directory = component_dir(app_config)
        if directory in seen:
            continue
        seen.add(directory)

        entry = _sections.get(directory, {})
        name = entry.get('name') or os.path.basename(directory)
        revision = get_revision(directory)
        sections.append({
            'name': name,
            'version': entry.get('version'),
            'template': _loadable(entry.get('template')),
            'revision': revision['short'] if revision else None,
            'revision_url': revision['url'] if revision else None,
            'anchor': slugify(name),
        })
    return sections


def first_party_app_configs():
    """Every installed app this deployment is actually made of, in INSTALLED_APPS order.

    The About page is an inventory of these, and so is a full test run -- which is why this
    is public rather than folded into `get_about_sections`. `mutint_common.test_runner` uses
    it to decide what to run when no labels are given, because in an assembled project
    nothing else can: the submodule directories have hyphens in their names, so they are not
    importable packages and unittest discovery cannot descend into them.
    """
    from django.apps import apps

    return [cfg for cfg in apps.get_app_configs() if _is_first_party(cfg)]


def _is_first_party(app_config):
    """Whether an app ships with this deployment rather than being a dependency of it.

    Decided by where the code lives, not by a list of names anyone has to keep up to date: a
    dependency is installed into site-packages (or dist-packages), and Django's own apps are
    under the django package. Everything else is something this deployment is made of, which
    is what the page is an inventory of.
    """
    if app_config.name.startswith('django.'):
        return False
    parts = os.path.abspath(app_config.path).replace(os.sep, '/').split('/')
    return 'site-packages' not in parts and 'dist-packages' not in parts


def _loadable(template):
    """`template` if it can be found, else None and a warning.

    A section whose template is missing renders as a bare heading -- which is what a
    component with no prose renders as anyway -- rather than raising. nav_registry takes the
    same line with a url_name that will not reverse: one app's mistake must not take down a
    page that is mostly other apps' content.
    """
    if not template:
        return None

    from django.template import TemplateDoesNotExist, loader

    try:
        loader.get_template(template)
    except TemplateDoesNotExist:
        logger.warning("About section template %r is registered but does not exist", template)
        return None
    return template

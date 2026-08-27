"""Collecting one manual out of whatever an assembled project is made of.

`./aledb docs` in aledb-core builds aledb-core's documentation. The same command in an
assembled project should build *that project's* manual, and what belongs in it is what the
project installs: its own pages, plus every installed component's.

This is the shape the rest of the codebase already uses. `requirements.txt` and `tools.txt` are
gathered from every component; templates and static files are discovered across installed apps;
the About page is an inventory keyed by the checkout each app came from. There is no eighth
registry here either -- a component contributes by having a `docs/` directory and a
`mkdocs.yml`, and an uninstalled submodule contributes nothing because its apps are not
installed.

**The manual is organised by audience, not by component.** A reader arrives knowing whether
they want to use ALEdb or extend it, and rarely wants "everything aledb-fixation has to say".
So a component files its pages under one of the headings below, in its own nav, and the
collector merges each heading across every component.
"""

import os
import shutil

#: The three sections a manual has. A component's `mkdocs.yml` nav uses these as top-level
#: headings to say who each page is for. Spelled once here, the way `rebuild_registry` spells
#: its `INPUT_*` vocabulary, so a typo in a component is visible as pages landing in the wrong
#: half rather than as a silent misfile.
USING = "Using ALEdb"
EXTENDING = "Extending ALEdb"
#: Where a component's own story goes -- and the fallback for anything filed under a heading
#: this module does not recognise. Unrecognised is not an error: a component that has not
#: thought about audience still builds, and its pages appear under its own name here rather
#: than being dropped.
DEPLOYMENT = "About this deployment"

AUDIENCES = (USING, EXTENDING, DEPLOYMENT)

#: Where the merged tree is assembled. Git-ignored; the sources are each component's `docs/`.
BUILD_DIRNAME = ".docs-build"


def project_root():
    """The directory of the project being run, or None if no entry script exported it.

    `ALEDB_TOOLS_DIR` is `<project root>/env/tools`, exported by `./aledb` or `./mutint` before
    it re-execs. It is the only thing that knows: settings cannot, because an assembled project
    reaches `get_base_settings()` through aledb-core's `config/defaults.py`, which passes the
    *aledb-core* directory -- the same trap `templates/` and `staticfiles/` work around.
    """
    tools_dir = os.environ.get("ALEDB_TOOLS_DIR")
    if not tools_dir:
        return None
    return os.path.dirname(os.path.dirname(os.path.abspath(tools_dir)))


def contributing_components(root):
    """`[(directory_name, directory), ...]` in INSTALLED_APPS order.

    A component contributes when it has both `docs/` and `mkdocs.yml`. Missing either it is
    skipped in silence, because most components will never have documentation and that is not
    a fault worth reporting on every build.

    **The project itself is excluded**, even though its own apps are installed. Standalone,
    aledb-core *is* the project, and without this it would be collected as a component of
    itself -- every page appearing twice, once at the top level and once nested.
    """
    from aledb_common.about_registry import component_dir, first_party_app_configs

    seen = []
    for app_config in first_party_app_configs():
        directory = component_dir(app_config)
        if directory in seen:
            continue
        seen.append(directory)

    found = []
    for directory in seen:
        if root and os.path.normpath(directory) == os.path.normpath(root):
            continue
        if not os.path.isdir(os.path.join(directory, "docs")):
            continue
        if not os.path.isfile(os.path.join(directory, "mkdocs.yml")):
            continue
        found.append((os.path.basename(directory.rstrip(os.sep)), directory))
    return found


def read_config(directory):
    """A component's own `mkdocs.yml`, as a dict.

    PyYAML is imported here rather than at module scope: it arrives with mkdocs, which lives in
    `requirements-docs.txt` and is not installed in a normal environment. Everything above this
    point has to keep working without it, and the tests depend on that.
    """
    import yaml

    path = os.path.join(directory, "mkdocs.yml")
    with open(path, encoding="utf-8") as handle:
        # `mkdocs.yml` can carry Python-specific tags (`!!python/name:` for a hook); this only
        # ever reads `nav` and `site_name`, so the safe loader is enough and the unsafe one
        # would execute a component's config.
        return yaml.safe_load(handle) or {}


def prefix_paths(nav, prefix):
    """A nav with every file path moved under `prefix/`.

    A nav entry is a bare path, or `{title: path}`, or `{title: [more entries]}`. Recursing
    rather than pattern-matching on strings because a section can nest arbitrarily and a
    component's own structure has to survive the move intact.
    """
    if isinstance(nav, str):
        return "%s/%s" % (prefix, nav)
    if isinstance(nav, list):
        return [prefix_paths(entry, prefix) for entry in nav]
    if isinstance(nav, dict):
        return {title: prefix_paths(value, prefix) for title, value in nav.items()}
    return nav


def _entries_of(item):
    """`(title, children)` for a nav item, or None if it is a bare path."""
    if isinstance(item, dict) and len(item) == 1:
        (title, value), = item.items()
        return title, value
    return None


def merge_navs(project_config, components):
    """The manual's nav: the three audience sections, filled from every contributor.

    The project goes first within each section, then components in INSTALLED_APPS order, and
    each contributor's own ordering survives inside its part. An empty section is omitted
    rather than rendered as a heading with nothing under it.
    """
    buckets = {name: [] for name in AUDIENCES}

    def absorb(config, prefix, label):
        for item in config.get("nav") or []:
            entry = _entries_of(item)
            if entry and entry[0] in AUDIENCES:
                title, children = entry
                buckets[title].extend(
                    prefix_paths(children, prefix) if prefix else children)
            else:
                # Not filed under a recognised audience. It goes under the deployment
                # section, named for whoever contributed it, rather than being dropped.
                moved = prefix_paths(item, prefix) if prefix else item
                buckets[DEPLOYMENT].append({label: [moved]} if prefix else moved)

    absorb(project_config, None, None)
    for name, directory in components:
        absorb(read_config(directory), name, name)

    return [{name: buckets[name]} for name in AUDIENCES if buckets[name]]


def build_config(project_root_dir, project_config, components):
    """The generated `mkdocs.yml` for the merged manual, as a dict."""
    config = dict(project_config)
    # Relative to the generated config, which sits beside the tree rather than inside it --
    # mkdocs refuses a `docs_dir` that is the config file's own directory.
    config["docs_dir"] = "docs"
    config["site_dir"] = os.path.join(project_root_dir, "site")
    config["nav"] = merge_navs(project_config, components)

    # Every contributing component, absolutely, or a plugin's `::: aledb_yourthing.util` does
    # not resolve once its pages are being built from somewhere else.
    paths = [project_root_dir] + [directory for _, directory in components]
    for plugin in config.get("plugins") or []:
        if isinstance(plugin, dict) and "mkdocstrings" in plugin:
            handlers = plugin["mkdocstrings"].setdefault("handlers", {})
            handlers.setdefault("python", {})["paths"] = paths

    # `mkdocs serve` watches `docs_dir`; the component trees are symlinks into repositories it
    # would otherwise never look at.
    config["watch"] = [os.path.join(directory, "docs") for _, directory in components]
    return config


def assemble(project_root_dir, components):
    """Build the merged tree and write its config. Returns the config file's path.

    The component directories are **symlinked**, not copied: mkdocs walks `docs_dir` with
    `followlinks=True`, so it reads straight through to each repository's own files and a
    build can never serve a stale copy of somebody else's docs.
    """
    import yaml

    build_dir = os.path.join(project_root_dir, BUILD_DIRNAME)
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
    tree = os.path.join(build_dir, "docs")
    os.makedirs(tree)

    for entry in os.listdir(os.path.join(project_root_dir, "docs")):
        os.symlink(os.path.join(project_root_dir, "docs", entry),
                   os.path.join(tree, entry))
    for name, directory in components:
        os.symlink(os.path.join(directory, "docs"), os.path.join(tree, name))

    config = build_config(project_root_dir, read_config(project_root_dir), components)
    config_path = os.path.join(build_dir, "mkdocs.yml")
    with open(config_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, default_flow_style=False)
    return config_path

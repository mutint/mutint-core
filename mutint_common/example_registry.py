"""Registry of example datasets contributed by components.

A feature that needs a particular shape of data is undemonstrable without some, and the
absence looks identical to a bug. Fixed Mutations is the case that prompted this: a mutation
is fixated when it appears in both of the last two flasks of an ALE, so an ALE with one flask
can never produce one -- and the page then renders a full table with every sample column and
no rows, which is exactly what a broken page looks like too.

So a component ships data that exercises its own feature, and core provides one command to
load it. Components register in ``AppConfig.ready()``::

    register_example_dataset(
        'mutint-compare-fixation-example',
        os.path.join(os.path.dirname(__file__), 'examples', 'fixation'),
        description='Two ALE lineages over four flasks, with mutations that arrive and stay.')

**A dataset is a directory of files the import path already understands** -- nothing more, and
in particular not a Django fixture. Loading one therefore runs the registered import handlers
in priority order, exactly as a drop on the Add Data page does, and ends in
``gd_import.run_post_processing``, which fires the post-experiment hooks. So the derived data
the example exists to show is genuinely computed rather than shipped alongside. A fixture
would load faster and prove nothing about the pipeline.

Ordering is cosmetic here -- datasets are listed for a human to pick from, never run as a
sequence -- so unlike ``import_registry`` there is no priority. Names are sorted for a stable
listing. Duplicate names raise, as `register_import_handler` does: one component silently
shadowing another's example is worse than a startup error.
"""

_example_datasets = {}


def register_example_dataset(name, directory, description="", component=None):
    """Register a named example dataset.

    name        stable slug, and what `./mutint load_example` takes. Hyphens are fine and
                conventional here -- it is a registry string, not a module name.
    directory   absolute path to the files, laid out as they would be dropped on the Add
                page. Not read until the dataset is loaded, so a component with data it
                ships conditionally can still register unconditionally. A README and any
                dotfiles are skipped when loading, so the expected answer can live beside
                the data that produces it.
    description one line, shown in the listing. Say what shape the data is, since that is
                the thing the loader cannot tell from the files.
    component   the repo it belongs to, for the listing. Defaults to the leading part of
                `name`, which by convention is the component.
    """
    if name in _example_datasets:
        raise ValueError("example dataset %r is already registered" % (name,))
    _example_datasets[name] = {
        "name": name,
        "directory": directory,
        "description": description,
        "component": component or name.split("-example")[0],
    }


def get_example_datasets():
    """Every registered dataset, sorted by name."""
    return [_example_datasets[name] for name in sorted(_example_datasets)]


def get_example_dataset(name):
    """One dataset, or None."""
    return _example_datasets.get(name)

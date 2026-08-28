from django.apps import AppConfig


class FilterConfig(AppConfig):
    name = "aledb_filter"

    # Nothing to register. A `Filter` nav entry pointed at `/filter`, an editing page that wrote
    # one shared `AleExperimentFilter` row per experiment, and an `experiment_filter` rebuilder
    # that created that row's defaults. All three are gone: filtering is per-reader now, lives in
    # the session, and is applied by the page that reads it. There is no stored value to default,
    # to keep fresh, or to send anybody to a page to edit. See `aledb_filter/view_filter.py`.

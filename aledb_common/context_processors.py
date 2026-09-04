from django.conf import settings

from aledb_common.version import __version__


def branding(request):
    """Two separate things, deliberately.

    `branding` is the deployment's identity -- its name, version and logo. It is
    whatever ALEDB_BRANDING says and is empty by default, so an unconfigured
    aledb-core renders no name and no icon at all.

    `aledb_version` is aledb-core's own version, which is not configurable: it
    feeds the "Powered by ALEdb" watermark that every deployment carries.
    """
    return {
        'branding': getattr(settings, 'ALEDB_BRANDING', {}),
        'aledb_version': __version__,
    }


def global_settings(request):
    return {
        'GOOGLE_ANALYTICS_TAG': settings.GOOGLE_ANALYTICS_TAG,
    }


def plugin_exports(request):
    from aledb_common.plugin_registry import get_export_types
    return {
        'plugin_export_types': get_export_types(),
    }


def nav_items(request):
    from aledb_common.nav_registry import (
        EXPERIMENT_SECTION, MAIN_SECTION, get_nav_items,
    )
    return {
        'nav_main_items': get_nav_items(MAIN_SECTION),
        'nav_experiment_items': get_nav_items(EXPERIMENT_SECTION),
    }


def request_vocabulary(request):
    """The query-string names and values templates have to spell out.

    A template writes `name="population"` and `value="mixed"` as literals, which is fine until
    one of those words changes meaning. One already has: the mixed-sample token was
    `population`, which is now the model one level up and is what `?population=` becomes, so for
    one commit the same word meant two different things. Routing it through here is what let
    it change in one place -- a literal in a template is the kind of site a Python grep does
    not see and a rename tool cannot reach.

    So the names live in `constants.py` and arrive here. Views need not pass them, and the
    rename is an edit of one module rather than an audit of every form.
    """
    from aledb_common import constants
    return {
        'PARAM_EXPERIMENT': constants.REQUEST_EXPERIMENT_ID,
        'PARAM_POPULATION': constants.REQUEST_POPULATION,
        'PARAM_SAMPLE_TYPE': constants.REQUEST_SAMPLE_TYPE,
        'PARAM_ALL': constants.REQUEST_ALL,
        'SAMPLE_TYPE_CLONAL': constants.SAMPLE_TYPE_CLONAL,
        'SAMPLE_TYPE_MIXED': constants.SAMPLE_TYPE_MIXED,
    }

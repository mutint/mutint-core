from django.http import HttpResponse

from mutint_common.util import get_user_context

from django.template import TemplateDoesNotExist, loader
from mutint_common.logger import user_extra
from mutint_common.rebuild_registry import ensure_fresh
from mutint_dashboard.models import InstallationCounts
from mutint_dashboard.util import counts
from mutint_dashboard.views import get_general_count_dict, type_rows
# from mutint_search.views import MUT_TYPES, MUT_TYPES_DISPLAY, STRAINS, REF_SEQS
from mutint_search.views import MUT_TYPES, MUT_TYPES_DISPLAY, load_strains, load_ref_sequences
from mutint_experiment.utils import get_user_projects


import logging

from mutint_experiment.models import Experiment
from mutint_experiment.views import projects


logger = logging.getLogger(__name__)

# A deployment supplies this to take over the landing page; mutint-core ships no
# such template, so by default `/` is the project list.
SPLASH_TEMPLATE = "home/splash.html"


def get_unique_publication_count():
    return Experiment.objects.order_by().values('doi').distinct().count()


def home(request):
    try:
        template = loader.get_template(SPLASH_TEMPLATE)
    except TemplateDoesNotExist:
        # No splash configured -- show the projects, which is what a bare mutint-core
        # has to show. Rendered in place rather than redirected, so `/` stays `/`.
        return projects(request)

    # The same pair the dashboard calls, and for the same reason: marking is cheap and
    # running is not, so whichever of the two pages is read first pays once. This page had
    # neither, so a deployment whose splash is the only page anybody opens served whatever
    # the last rebuild left -- indefinitely, since nothing else here would ever refresh it.
    ensure_fresh('sample_counts')
    ensure_fresh('mutation_counts')

    general_count_dict = get_general_count_dict()
    calls = counts(InstallationCounts.MUTATION_CALLS)
    unique = counts(InstallationCounts.UNIQUE_MUTATIONS)

    general_count_dict['observed'] = calls.get('total', 0)
    general_count_dict['unique'] = unique.get('total', 0)

    context = get_user_context(request.user)
    # A deployment's splash renders the mutation types the same way the dashboard does, so it
    # gets the same rows rather than a second set of bindings to keep in step. It was the
    # divergence between these two that left nine cells blank on the live page: this view's
    # context key was renamed and only the dashboard's template followed.
    context.update({"count_dict": general_count_dict,
                    "call_total": calls.get('total', 0),
                    "unique_total": unique.get('total', 0),
                    "type_rows": type_rows(calls, unique)})
    user_projects = get_user_projects(request.user)
    context.update({"mut_types": MUT_TYPES,
                    "strains": load_strains(),
                    "ref_seqs": load_ref_sequences(),
                    "projects": user_projects})
    logger.info("home", extra=user_extra(request))
    context.update({"unique_publication_count": get_unique_publication_count()})
    return HttpResponse(template.render(context, request), content_type="text/html")

from django.http import HttpResponse

from aledb_common.util import get_user_context

from django.template import TemplateDoesNotExist, loader
from aledb_common.logger import user_extra
from aledb_dashboard.models import ObservedMutationCounts, UniqueMutationCounts
from aledb_dashboard.views import get_general_count_dict
# from aledb_search.views import MUT_TYPES, MUT_TYPES_DISPLAY, STRAINS, REF_SEQS
from aledb_search.views import MUT_TYPES, MUT_TYPES_DISPLAY, load_strains, load_ref_sequences
from aledb_experiment.utils import get_user_projects


import logging

from aledb_experiment.models import Experiment
from aledb_experiment.views import projects


logger = logging.getLogger(__name__)

# A deployment supplies this to take over the landing page; aledb-core ships no
# such template, so by default `/` is the project list.
SPLASH_TEMPLATE = "home/splash.html"


def get_unique_publication_count():
    return Experiment.objects.order_by().values('doi').distinct().count()


def home(request):
    try:
        template = loader.get_template(SPLASH_TEMPLATE)
    except TemplateDoesNotExist:
        # No splash configured -- show the projects, which is what a bare aledb-core
        # has to show. Rendered in place rather than redirected, so `/` stays `/`.
        return projects(request)

    general_count_dict = get_general_count_dict()
    observed_mutation_counts = ObservedMutationCounts.objects.first()
    unique_mutation_counts = UniqueMutationCounts.objects.first()

    if unique_mutation_counts and observed_mutation_counts:
        general_count_dict['observed'] = observed_mutation_counts.total
        general_count_dict['unique'] = unique_mutation_counts.total

    context = get_user_context(request.user)
    context.update({"count_dict": general_count_dict,
                    "unique_mutation_counts": unique_mutation_counts,
                    "observed_mutation_counts": observed_mutation_counts})
    user_projects = get_user_projects(request.user)
    context.update({"mut_types": MUT_TYPES,
                    "strains": load_strains(),
                    "ref_seqs": load_ref_sequences(),
                    "projects": user_projects})
    logger.info("home", extra=user_extra(request))
    context.update({"unique_publication_count": get_unique_publication_count()})
    return HttpResponse(template.render(context, request), content_type="text/html")

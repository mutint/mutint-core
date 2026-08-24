"""The About page.

It contributes no prose of its own. Every component installed in this deployment gets a
heading, and the ones that registered a template get a body under it -- see
aledb_common/about_registry.py. That is what lets a plugin describe itself without aledb-core
knowing it exists, and what makes the page an inventory of what is actually running.
"""

import logging

from django.http import HttpResponse
from django.template import loader

from aledb_common.about_registry import get_about_sections
from aledb_common.logger import user_extra
from aledb_common.util import get_user_context

logger = logging.getLogger(__name__)


def about(request):
    logger.info("about", extra=user_extra(request))

    context = get_user_context(request.user)
    context["sections"] = get_about_sections()

    template = loader.get_template("about/index.html")
    return HttpResponse(template.render(context, request), content_type="text/html")

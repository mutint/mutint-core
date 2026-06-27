from django.shortcuts import render

from aledb_common.util import get_user_context
from aledb_common.logger import user_extra
import logging

logger = logging.getLogger(__name__)
def bibliome(request):
    logger.info("bibliome usage", extra = user_extra(request))
    try:
        return render(request, "bibliome/index.html", get_user_context(request.user), content_type="text/html")
    except Exception as e:
        logger.exception("bibliome broke", extra = user_extra(request))

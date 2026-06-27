from django.http import HttpResponseRedirect
from django.conf import settings
from re import compile
import logging

EXEMPT_URLS = [compile(settings.LOGIN_URL.lstrip('/'))]
if hasattr(settings, 'LOGIN_EXEMPT_URLS'):
    EXEMPT_URLS += [compile(expr) for expr in settings.LOGIN_EXEMPT_URLS]


class LoginRequiredMiddleware:
    """Redirect unauthenticated users to LOGIN_URL for all paths except LOGIN_EXEMPT_URLS."""

    def process_request(self, request):
        logger = logging.getLogger(__name__)
        assert hasattr(request, 'user'), (
            "LoginRequiredMiddleware requires AuthenticationMiddleware in MIDDLEWARE."
        )
        if not request.user.is_authenticated():
            path = request.path_info.lstrip('/')
            if not any(m.match(path) for m in EXEMPT_URLS):
                logger.debug("Unauthenticated request, redirecting to login")
                return HttpResponseRedirect(settings.LOGIN_URL)

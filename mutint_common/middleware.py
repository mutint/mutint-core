import logging
import re

from django.conf import settings
from django.http import HttpResponseRedirect

logger = logging.getLogger(__name__)


class LoginRequiredMiddleware:
    """Redirect unauthenticated users to LOGIN_URL for all paths except LOGIN_EXEMPT_URLS.

    Requires django.contrib.auth.middleware.AuthenticationMiddleware to be installed
    ahead of this one in MIDDLEWARE.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Built once, when Django assembles the middleware chain. Patterns are
        # matched against request.path_info with its leading slash stripped, so
        # LOGIN_URL is stripped the same way.
        self.exempt_urls = [re.compile(settings.LOGIN_URL.lstrip('/'))]
        self.exempt_urls += [re.compile(expr)
                             for expr in getattr(settings, 'LOGIN_EXEMPT_URLS', [])]

    def __call__(self, request):
        assert hasattr(request, 'user'), (
            "LoginRequiredMiddleware requires AuthenticationMiddleware in MIDDLEWARE."
        )

        if not request.user.is_authenticated:
            path = request.path_info.lstrip('/')
            if not any(pattern.match(path) for pattern in self.exempt_urls):
                logger.debug("Unauthenticated request, redirecting to login")
                return HttpResponseRedirect(settings.LOGIN_URL)

        return self.get_response(request)

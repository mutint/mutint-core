"""Signing a local install in as its only account, without being asked.

`./mutint start` creates `admin` / `admin` and opens a browser on a site that then asks for
that password. On somebody's own laptop, running a single-user program, that is a prompt
with one possible answer. So an anonymous page load from this machine is signed in as the
admin, for as long as the admin is the only account there is.

Three things decide it, and each is a guard rather than a feature:

- **`MUTINT_AUTO_LOGIN`**, absent meaning True -- right for anything installed from the
  public repository. A hosted deployment sets it False (ALEdb does), as it does
  `MUTINT_UPDATE_ENABLED`.
- **Exactly one user, and a superuser.** The moment a second account is created, in Django
  admin or by `createsuperuser`, this stops and the login page is what everyone meets.
  Counting is the whole rule: no username is checked, so renaming `admin` changes nothing,
  and no password is, because a changed password is not what the person asked to be the
  switch.
- **Loopback.** `runserver` binds 127.0.0.1 anyway; this is what keeps a
  `runserver 0.0.0.0` from signing in whoever reaches it.

Two consequences are easy to get wrong:

- **GET and HEAD only.** `auth.login` rotates the CSRF token, and `CsrfViewMiddleware`
  checks a POST in `process_view`, *after* every middleware's pre-phase -- so a POST that
  arrived anonymous and was signed in here would fail its own CSRF check. A sign-in belongs
  to a page load, not to a write.
- **Logging out has to stick**, or the Logout button lands you straight back in.
  `DecliningLogoutView` leaves a key in the fresh session that `auth.logout` creates, and the
  middleware honours it. `SESSION_EXPIRE_AT_BROWSER_CLOSE` is True, so closing the browser
  clears the decline and the next visit signs in again -- which is the "by default" asked
  for.

The test runner turns the setting off for the whole suite: the test client's `REMOTE_ADDR` is
loopback, and any test that creates one superuser and then probes a page anonymously would
otherwise become a signed-in superuser without saying so. `test_auto_login.py` turns it back
on for itself.
"""

import logging

from django.conf import settings
from django.contrib import auth
from django.contrib.auth import views as auth_views

logger = logging.getLogger(__name__)

SETTING = "MUTINT_AUTO_LOGIN"
DECLINED_SESSION_KEY = "mutint_auto_login_declined"
LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")
# The login page and the logout POST are left alone: one is where a person signs in as
# somebody else, the other is the one request that must not end signed in.
EXEMPT_PREFIX = "/accounts/"


def enabled():
    return bool(getattr(settings, SETTING, True))


def lone_admin():
    """The one user, when there is exactly one and it is an active superuser; else None."""
    User = auth.get_user_model()
    if User.objects.count() != 1:
        return None
    user = User.objects.first()
    if user.is_active and user.is_superuser:
        return user
    return None


def is_loopback(request):
    return request.META.get("REMOTE_ADDR") in LOOPBACK_ADDRESSES


class AutoLoginMiddleware:
    """Sign an anonymous loopback page load in as the lone admin, then carry on.

    After AuthenticationMiddleware, which supplies `request.user`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        assert hasattr(request, "user"), (
            "AutoLoginMiddleware requires AuthenticationMiddleware in MIDDLEWARE."
        )
        if self._should_sign_in(request):
            user = lone_admin()
            if user is not None:
                auth.login(request, user)
                logger.debug("signed %s in automatically", user.username)
        return self.get_response(request)

    @staticmethod
    def _should_sign_in(request):
        if request.method not in ("GET", "HEAD"):
            return False
        if request.user.is_authenticated:
            return False
        if not enabled():
            return False
        if not is_loopback(request):
            return False
        if request.path_info.startswith(EXEMPT_PREFIX):
            return False
        if request.session.get(DECLINED_SESSION_KEY):
            return False
        return True


class DecliningLogoutView(auth_views.LogoutView):
    """Django's logout, remembering in the new session that this browser chose to leave."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        # `auth.logout` flushed the old session; setting a key makes a fresh one, saved by
        # SessionMiddleware on the way out.
        request.session[DECLINED_SESSION_KEY] = True
        return response

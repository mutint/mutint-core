"""The account pages every auth app serves: login, logout and changing a password.

**Shared because the auth slot is pluggable.** An app carrying `auth_app = True` is what
`mutint_common.urls` mounts under `^accounts/` with namespace `accounts`, and this module is
what that app serves. Only one is installed at a time, so a deployment replacing
authentication replaces the app and inherits these routes rather than restating them.

`mutint_accounts_noauth` is the only occupant in this repo. There were two: `mutint_accounts`
existed to be the one carrying django-defender's brute-force protection, and when defender
went it was left identical to the default -- an alternative that was not one.

The consolidation is why this module exists, and the reason outlives the second app. The two
were hand-written lists that had already drifted into live bugs nothing exercised:
`mutint_accounts` passed `{'next_page': '/'}` as `re_path`'s *extra kwargs* rather than to
`as_view()`, raising `TypeError` on the first login, and it shipped no templates at all, so
swapping to it also meant `TemplateDoesNotExist`. Anything added here reaches whatever occupies
the slot next, which is the point.

Two things in here are load-bearing and invisible at the call site:

- **`success_url` on the password change is not optional.** `PasswordChangeView`'s default is
  `reverse_lazy("password_change_done")`, which is *un-namespaced*, and these patterns are
  included under `namespace='accounts'`. Left to the default it raises `NoReverseMatch` on a
  **successful** change -- the new password is saved and then the redirect 500s, which is the
  worst possible place for it to fail.

- **No template here is named `registration/...`, and that is deliberate.**
  `django.contrib.admin` ships `registration/password_change_form.html` and
  `registration/password_change_done.html`, and it is *first* in INSTALLED_APPS, so with
  `APP_DIRS=True` its copies win over any later app's. A page named that way renders admin's
  version, in admin chrome, with no error anywhere. Naming ours `accounts/...` sidesteps the
  race rather than depending on app order. `accounts/login.html` follows the same convention
  even though `registration/login.html` happens to be safe today.

`next_page='/'` is kept exactly as both apps had it. It is also why `LOGIN_REDIRECT_URL` and
`LOGOUT_REDIRECT_URL` in `base_settings.py` are inert: a `next_page` on the view wins over
both. Changing that is a behavior change and not this module's business.
"""

from django.contrib.auth import views as auth_views
from django.urls import re_path, reverse_lazy

account_urlpatterns = [
    re_path(r'^login/$',
            auth_views.LoginView.as_view(
                template_name='accounts/login.html',
                next_page='/'),
            name='login'),
    re_path(r'^logout/$',
            auth_views.LogoutView.as_view(next_page='/'),
            name='logout'),
    re_path(r'^password/$',
            auth_views.PasswordChangeView.as_view(
                template_name='accounts/password_change.html',
                success_url=reverse_lazy('accounts:password_change_done')),
            name='password_change'),
    re_path(r'^password/done/$',
            auth_views.PasswordChangeDoneView.as_view(
                template_name='accounts/password_change_done.html'),
            name='password_change_done'),
]

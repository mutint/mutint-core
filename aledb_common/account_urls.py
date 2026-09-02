"""The account pages every auth app serves: login, logout and changing a password.

**Shared because the auth slot is pluggable.** Two apps carry `auth_app = True` --
`aledb_accounts_noauth` (the default) and `aledb_accounts` (production, django-defender) -- and
only one is installed at a time, so a route added to one is simply missing from the other.
`aledb_common.urls` includes whichever app it finds under `^accounts/` with namespace
`accounts`; this module is what that app serves.

They used to be two hand-written lists, and they had already drifted into two live bugs that
nothing exercised: `aledb_accounts` passed `{'next_page': '/'}` as `re_path`'s *extra kwargs*
rather than to `as_view()`, which raises `TypeError: login() got an unexpected keyword argument
'next_page'` on the first request, and it had no templates directory at all, so swapping to it
also meant `TemplateDoesNotExist`. Neither app has any reason to differ here -- django-defender
patches `LoginView.dispatch` from middleware rather than from the URLconf -- so there is one
list and both apps use it.

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
both. Changing that is a behaviour change and not this module's business.
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

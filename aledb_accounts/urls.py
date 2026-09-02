"""The production auth slot: the same routes, with django-defender watching logins.

The brute-force protection is applied by `defender_middleware`, which patches
`LoginView.dispatch` -- not from here. So this app's URLconf has no reason to differ from
`aledb_accounts_noauth`'s, and after they drifted into two separate bugs it no longer does:
both serve `aledb_common.account_urls`, which is where the reasoning lives.

What drifted: `next_page` was passed as `re_path`'s extra-kwargs dict rather than to
`as_view()`, so it arrived as a view keyword argument and raised `TypeError` on every login;
and this app ships no templates at all, so `registration/login.html` did not exist for it. Both
are gone with the consolidation rather than fixed in place.
"""

from aledb_common.account_urls import account_urlpatterns

app_name = 'accounts'

urlpatterns = list(account_urlpatterns)

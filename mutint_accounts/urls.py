"""The default auth slot: Django's own login and logout, with nothing enforced.

The routes themselves are shared -- see `mutint_common/account_urls.py`, which explains why
both auth apps serve the identical list and what breaks if the templates are renamed.
"""

from mutint_common.account_urls import account_urlpatterns

app_name = 'accounts'

# list(), so an auth app may append routes of its own without mutating the shared list.
urlpatterns = list(account_urlpatterns)

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.conf import settings
from mutint_common.urls import get_core_urlpatterns

urlpatterns = get_core_urlpatterns()

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()

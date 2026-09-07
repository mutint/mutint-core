from django.urls import re_path

from mutint_upgrade import views

urlpatterns = [
    re_path(r'^$', views.upgrade_page, name="upgrade"),
    re_path(r'^check$', views.upgrade_check, name="upgrade_check"),
    re_path(r'^install$', views.upgrade_install, name="upgrade_install"),
]

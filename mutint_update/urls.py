from django.urls import re_path

from mutint_update import views

urlpatterns = [
    re_path(r'^$', views.update_page, name="update"),
    re_path(r'^check$', views.update_check, name="update_check"),
    re_path(r'^install$', views.update_install, name="update_install"),
    re_path(r'^restart$', views.update_restart, name="update_restart"),
]

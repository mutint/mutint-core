from django.urls import include, re_path

from mutint_home import views


urlpatterns = [
    re_path('^$', views.home, name="home"),
]

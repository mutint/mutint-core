from django.urls import include, re_path

from mutint_dashboard import views

urlpatterns = [re_path(r'^$', views.dashboard, name="dashboard")]
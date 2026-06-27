from django.contrib.auth import views as auth_views
from django.urls import re_path

app_name = 'accounts'

urlpatterns = [
    re_path(r'^login/$', auth_views.LoginView.as_view(next_page='/'), name='login'),
    re_path(r'^logout/$', auth_views.LogoutView.as_view(next_page='/'), name='logout'),
]

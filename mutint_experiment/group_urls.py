"""Routes for a user group, mounted at ``/group/``.

Out from under `/ale/` for the same reason as projects, and more plainly: a group is people.
The model was renamed from `AleGroup` for exactly this.
"""

from django.urls import re_path

from mutint_experiment.group_views import (
    group_create, group_delete, group_detail, group_member_add, group_member_remove,
    group_member_update, group_new, group_transfer, group_update, groups,
)

urlpatterns = [
    re_path(r'^$', groups, name="groups_view"),
    re_path(r'^new/$', group_new, name="group_new"),
    re_path(r'^create/$', group_create, name="group_create"),

    re_path(r'^(?P<pk>[0-9]+)/$', group_detail, name="group_detail"),
    re_path(r'^(?P<pk>[0-9]+)/update/$', group_update, name="group_update"),
    re_path(r'^(?P<pk>[0-9]+)/delete/$', group_delete, name="group_delete"),
    re_path(r'^(?P<pk>[0-9]+)/transfer/$', group_transfer, name="group_transfer"),
    re_path(r'^(?P<pk>[0-9]+)/members/add/$', group_member_add, name="group_member_add"),
    re_path(r'^(?P<pk>[0-9]+)/members/update/$',
            group_member_update, name="group_member_update"),
    re_path(r'^(?P<pk>[0-9]+)/members/remove/$',
            group_member_remove, name="group_member_remove"),
]

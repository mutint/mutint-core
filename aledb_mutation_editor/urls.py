from django.urls import re_path

from aledb_mutation_editor import views

# Mounted at ^mutation-editor/ (aledb_common/urls.py). Deliberately not under ^mutations/,
# which is aledb_seq's and names the read-only tables, nor under ^mutation-table/, which is
# the curation endpoints every table posts to. This app owns a different thing: the stored
# mutations themselves, rather than a way of looking at them.
urlpatterns = [
    re_path(r'^$', views.mutation_editor, name='mutation_editor'),
    re_path(r'^add$', views.mutation_add, name='mutation_add'),
    re_path(r'^copy$', views.mutation_copy, name='mutation_copy'),
    re_path(r'^change$', views.mutation_change, name='mutation_change'),
    re_path(r'^history$', views.mutation_history, name='mutation_history'),

    # Writes. Separate from the pages above for the reason `aledb_experiment.sample_views`
    # gives: the page renders and checks permission because people reach its URL directly,
    # and the endpoint checks again because that is where the write happens.
    re_path(r'^delete$', views.mutation_delete, name='mutation_delete'),
    re_path(r'^add/apply$', views.mutation_add_apply, name='mutation_add_apply'),
    re_path(r'^copy/apply$', views.mutation_copy_apply, name='mutation_copy_apply'),
    re_path(r'^change/apply$', views.mutation_change_apply, name='mutation_change_apply'),
    re_path(r'^restore$', views.mutation_restore, name='mutation_restore'),
]

from django.urls import re_path

from aledb_mutation_editor import views

# Mounted at ^mutation-editor/ (aledb_common/urls.py). Deliberately not under ^mutations/,
# which is aledb_seq's and names the read-only tables, nor under ^mutation-table/, which is
# the curation endpoints every table posts to. This app owns a different thing: the stored
# mutations themselves, rather than a way of looking at them.
urlpatterns = [
    # The four things the toolbar offers, in the order it offers them. Editing and deleting
    # are two tabs over one listing rather than one page doing both: they are different
    # decisions about the same rows, and a page whose next button might mean either is a page
    # people click carefully. `^$` is the Edit tab because that is where the sidebar's
    # "Edit Mutations" link lands.
    re_path(r'^$', views.mutation_editor, name='mutation_editor'),
    re_path(r'^delete$', views.mutation_delete, name='mutation_delete'),
    re_path(r'^add$', views.mutation_add, name='mutation_add'),
    re_path(r'^copy$', views.mutation_copy, name='mutation_copy'),
    re_path(r'^history$', views.mutation_history, name='mutation_history'),

    # One mutation's own fields. Reached from a row of the Edit tab rather than from the
    # toolbar, because it needs a mutation to be about.
    re_path(r'^edit$', views.mutation_edit, name='mutation_edit'),

    # Writes. Separate from the pages above for the reason `aledb_experiment.sample_views`
    # gives: the page renders and checks permission because people reach its URL directly,
    # and the endpoint checks again because that is where the write happens.
    #
    # Every one of them is `<what>/apply`, deleting included -- it used to be a bare
    # `^delete$`, which was the odd one out and which the Delete *tab* now needs.
    re_path(r'^delete/apply$', views.mutation_delete_apply, name='mutation_delete_apply'),
    re_path(r'^add/apply$', views.mutation_add_apply, name='mutation_add_apply'),
    re_path(r'^copy/apply$', views.mutation_copy_apply, name='mutation_copy_apply'),
    re_path(r'^edit/apply$', views.mutation_edit_apply, name='mutation_edit_apply'),
    re_path(r'^restore$', views.mutation_restore, name='mutation_restore'),
]

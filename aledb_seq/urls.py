from django.urls import include, re_path
import aledb_seq.views.mutations


# TODO: Understand if '-' is better than "_" within a URL.
urlpatterns = [
    re_path(r'^$', aledb_seq.views.mutations.mutation_table, name="mutation_table"),
    re_path(r'^amplifications', aledb_seq.views.mutations.amplification_data, name="amplifications"),
    re_path(r'^add_to_global_filter', aledb_seq.views.mutations.add_to_global_filter, name='mutation_to_global_filter'),
    re_path(r'^add_to_exp_filter', aledb_seq.views.mutations.add_to_exp_filter, name='mutation_to_exp_filter'),
    re_path(r'^toggle-mut-tag/', aledb_seq.views.mutations.save_mut_tag, name='toggle_mut_tag'),
    re_path(r'^toggle-rep-tag', aledb_seq.views.mutations.save_rep_tag, name='toggle_rep_tag'),
    re_path(r'^evidence', aledb_seq.views.mutations.save_rep_tag, name='toggle_rep_tag'),

]

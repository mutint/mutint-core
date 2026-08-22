from django.urls import include, re_path
import aledb_seq.views.alignments
import aledb_seq.views.mutations


# TODO: Understand if '-' is better than "_" within a URL.
urlpatterns = [
    re_path(r'^$', aledb_seq.views.mutations.mutation_table, name="mutation_table"),
    re_path(r'^add_to_global_filter', aledb_seq.views.mutations.add_to_global_filter, name='mutation_to_global_filter'),
    re_path(r'^add_to_exp_filter', aledb_seq.views.mutations.add_to_exp_filter, name='mutation_to_exp_filter'),
    re_path(r'^toggle-mut-tag/', aledb_seq.views.mutations.save_mut_tag, name='toggle_mut_tag'),
    re_path(r'^toggle-rep-tag', aledb_seq.views.mutations.save_rep_tag, name='toggle_rep_tag'),
    re_path(r'^evidence', aledb_seq.views.mutations.save_rep_tag, name='toggle_rep_tag'),

    # Managed-store files, addressed by primary key and streamed with Range support.
    re_path(r'^alignments/(?P<reseq_id>\d+)/bam$',
            aledb_seq.views.alignments.sample_bam, name='sample_bam'),
    re_path(r'^alignments/(?P<reseq_id>\d+)/bai$',
            aledb_seq.views.alignments.sample_bai, name='sample_bai'),
    re_path(r'^reference/(?P<experiment_id>\d+)/fasta$',
            aledb_seq.views.alignments.reference_fasta, name='reference_fasta'),
    re_path(r'^reference/(?P<experiment_id>\d+)/fai$',
            aledb_seq.views.alignments.reference_fai, name='reference_fai'),
    re_path(r'^reference/(?P<experiment_id>\d+)/gff3$',
            aledb_seq.views.alignments.reference_gff3, name='reference_gff3'),
]

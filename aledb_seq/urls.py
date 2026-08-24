from django.urls import include, re_path
import aledb_seq.views.alignments
import aledb_seq.views.breseq_table
import aledb_seq.views.browse


# TODO: Understand if '-' is better than "_" within a URL.
urlpatterns = [
    # There is no `^$` here any more. The cross-sample table it used to serve is the
    # Compare page, which now lives in the aledb-compare plugin at /compare/ -- it is one
    # way of looking at an experiment, not a core function, and a deployment may leave it
    # out. The tag and filter endpoints that used to sit here moved to /mutation-table/
    # (aledb_seq/table_urls.py): every table page posts to them, not just Compare.
    re_path(r'^breseq$', aledb_seq.views.breseq_table.breseq_table, name="breseq_table"),

    # igv.js at one observed mutation's position, linked from the mutation table's cells.
    re_path(r'^browse$', aledb_seq.views.browse.browse_mutation, name='browse_mutation'),

    # Managed-store files, addressed by primary key and streamed with Range support.
    re_path(r'^alignments/(?P<reseq_id>\d+)/bam$',
            aledb_seq.views.alignments.sample_bam, name='sample_bam'),
    re_path(r'^alignments/(?P<reseq_id>\d+)/bai$',
            aledb_seq.views.alignments.sample_bai, name='sample_bai'),
    re_path(r'^alignments/(?P<reseq_id>\d+)/bigwig$',
            aledb_seq.views.alignments.sample_bigwig, name='sample_bigwig'),
    re_path(r'^reference/(?P<experiment_id>\d+)/fasta$',
            aledb_seq.views.alignments.reference_fasta, name='reference_fasta'),
    re_path(r'^reference/(?P<experiment_id>\d+)/fai$',
            aledb_seq.views.alignments.reference_fai, name='reference_fai'),
    re_path(r'^reference/(?P<experiment_id>\d+)/gff3$',
            aledb_seq.views.alignments.reference_gff3, name='reference_gff3'),
]

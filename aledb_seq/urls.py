from django.urls import include, re_path
import aledb_seq.views.alignments
import aledb_seq.views.breseq_table
import aledb_seq.views.browse
import aledb_seq.views.ncbi_view


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
    # What a click on the Mutations track calls: the page's state for a different mutation,
    # so the switch does not rebuild igv and re-fetch the reads to show a locus already up.
    re_path(r'^browse/at$', aledb_seq.views.browse.browse_at, name='browse_at'),

    # NCBI's Sequence Viewer at one mutation's locus, linked from the Reference Seq column.
    # A mutation rather than an observed one: this page draws no sample data, so the handle
    # is the row the contig name belongs to. `check` states an accession and verifies it.
    re_path(r'^ncbi$', aledb_seq.views.ncbi_view.ncbi_view, name='ncbi_view'),
    re_path(r'^ncbi/check$', aledb_seq.views.ncbi_view.ncbi_check, name='ncbi_check'),

    # What reference genome this experiment is called against, and which NCBI record each
    # of its sequences is. `^reference$` does not collide with the `^reference/<id>/...`
    # file routes below, which all carry an id segment.
    re_path(r'^reference$', aledb_seq.views.ncbi_view.reference_view, name='reference_view'),

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
    re_path(r'^reference/(?P<experiment_id>\d+)/chromalias$',
            aledb_seq.views.alignments.reference_chromalias, name='reference_chromalias'),
]

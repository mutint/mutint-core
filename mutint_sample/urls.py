from django.urls import include, re_path
import mutint_sample.views.alignments
import mutint_sample.views.breseq_table
import mutint_sample.views.browse
import mutint_sample.views.ncbi_view
import mutint_sample.views.report


# TODO: Understand if '-' is better than "_" within a URL.
urlpatterns = [
    # There is no `^$` here any more. The cross-sample table it used to serve is the
    # Compare page, which now lives in the mutint-compare plugin at /compare/ -- it is one
    # way of looking at an experiment, not a core function, and a deployment may leave it
    # out. The tag endpoints that used to sit here went with tagging itself.
    re_path(r'^breseq$', mutint_sample.views.breseq_table.breseq_table, name="breseq_table"),

    # breseq's own HTML report for one sample, kept at import. Two routes: the viewer, which
    # is a MutInt page framing it, and the files themselves. The file route takes a
    # client-named path because breseq's report links to whatever it wrote, and it is
    # contained by a realpath check rather than by a narrower pattern. Everything it serves is
    # sandboxed by a response header as well as by the frame -- see views/report.py.
    re_path(r'^report/(?P<sample_id>\d+)/$', mutint_sample.views.report.report,
            name='sample_report'),
    # The token is a path *segment* and must sit before the filename, so the report's own
    # relative links keep it: `breseq_icon.png` beside `index.html` resolves to the same
    # prefix. It is what authorises the request -- a sandboxed frame has an opaque origin and
    # never gets the session cookie. See views/report.py.
    re_path(r'^report/(?P<sample_id>\d+)/files/(?P<token>[^/]+)/(?P<path>.*)$',
            mutint_sample.views.report.report_file, name='sample_report_file'),

    # igv.js at one mutation call's position, linked from the mutation table's cells.
    re_path(r'^browse$', mutint_sample.views.browse.browse_mutation, name='browse_mutation'),
    # What a click on the Mutations track calls: the page's state for a different mutation,
    # so the switch does not rebuild igv and re-fetch the reads to show a locus already up.
    re_path(r'^browse/at$', mutint_sample.views.browse.browse_at, name='browse_at'),

    # NCBI's Sequence Viewer at one mutation's locus, linked from the Reference Seq column.
    # A mutation rather than an observed one: this page draws no sample data, so the handle
    # is the row the contig name belongs to. `check` states an accession and verifies it.
    re_path(r'^ncbi$', mutint_sample.views.ncbi_view.ncbi_view, name='ncbi_view'),
    re_path(r'^ncbi/check$', mutint_sample.views.ncbi_view.ncbi_check, name='ncbi_check'),

    # What reference genome this experiment is called against, and which NCBI record each
    # of its sequences is. `^reference$` does not collide with the `^reference/<id>/...`
    # file routes below, which all carry an id segment.
    re_path(r'^reference$', mutint_sample.views.ncbi_view.reference_view, name='reference_view'),

    # Managed-store files, addressed by primary key and streamed with Range support.
    re_path(r'^alignments/(?P<sample_id>\d+)/bam$',
            mutint_sample.views.alignments.sample_bam, name='sample_bam'),
    re_path(r'^alignments/(?P<sample_id>\d+)/bai$',
            mutint_sample.views.alignments.sample_bai, name='sample_bai'),
    re_path(r'^alignments/(?P<sample_id>\d+)/bigwig$',
            mutint_sample.views.alignments.sample_bigwig, name='sample_bigwig'),
    re_path(r'^reference/(?P<experiment_id>\d+)/fasta$',
            mutint_sample.views.alignments.reference_fasta, name='reference_fasta'),
    re_path(r'^reference/(?P<experiment_id>\d+)/fai$',
            mutint_sample.views.alignments.reference_fai, name='reference_fai'),
    re_path(r'^reference/(?P<experiment_id>\d+)/gff3$',
            mutint_sample.views.alignments.reference_gff3, name='reference_gff3'),
    re_path(r'^reference/(?P<experiment_id>\d+)/chromalias$',
            mutint_sample.views.alignments.reference_chromalias, name='reference_chromalias'),
]

"""The links core keeps about one sample: breseq's own report, and a curation check's.

Registered through `sample_link_registry` from `mutint_sample.apps`, the way a plugin's are,
so they sit in the sample box at the top of the Mutations page beside a FastQC report rather
than in a row of crosslinks of their own -- one place for everything kept about the sample.
Core's second use of its own seam, after the Overview's Storage panel.
"""

from django.urls import reverse


def breseq_report_links(sample, request=None):
    """`[(label, url, title), ...]`: the import's report and its summary when the sample
    has one, and the curation check's report when it has that. A `.gd` drop carries no
    report, so most samples of an older deployment get nothing here."""
    links = []
    if sample.report_stored:
        url = reverse("sample_report", kwargs={"sample_id": sample.pk})
        links.append(("breseq report", url,
                      "breseq's own report of this sample, with the evidence behind each call"))
        links.append(("breseq summary", url + "?page=summary.html",
                      "breseq's summary of the run this sample came from"))
    if sample.check_report_stored:
        links.append(("breseq check report",
                      reverse("sample_report", kwargs={"sample_id": sample.pk, "kind": "check"}),
                      "breseq's report of the curation check: the curated mutations applied "
                      "and the reads aligned against the result"))
    return links

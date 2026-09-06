"""The mutation matrix: mutations down, samples across, drawn the way breseq draws a mutation.

This is what Compare, Fixed Mutations, Converged Mutations and Search all render, and what a
plugin renders when it has "a set of mutation calls" to show. It replaced a builder that
produced positional arrays for a DataTable and a script that located the sample columns by
arithmetic on where the reference column sat -- arithmetic that went stale the first time the
fixed columns changed.

**The descriptive columns are the per-sample table's**, rendered by the same code
(`breseq_report.describe_mutation`, over `mutint_import.annotate.display`), so a mutation looks
the same whether one sample or forty are beside it. Each sample column holds that sample's call:
its frequency as breseq writes it, linked into the genome browser when the sample has reads,
blank when the sample does not carry the mutation. There is no descriptive Freq column, because
in a table with a column per sample every cell already is one.

**Rows are objects, not arrays.** DataTables reads a cell by name (`data: "gene"`,
`data: "samples.3"`), so hiding a column or adding one later moves no index anywhere. The sample
cells are compact -- `{"f": "42.0%", "s": 0.42, "p": true, "u": "..."}` -- because rows x
samples is the whole payload of the page.

What is deliberately not here: curation menus and a `user` argument. The matrix is a
read-only view; whoever wants to change the data has the mutation editor. A sample's flags
(hypermutator, contaminated, low coverage) are shown as badges, because they describe
the column; they are edited on the sample's own page.
"""

from dataclasses import dataclass, field
from typing import Optional

from django.urls import reverse

from mutint_sample.breseq_report import _frequency, describe_mutation
from mutint_sample.flags import flags_of
from mutint_sample.ncbi import verified_contig_names

#: What a present call with no recorded frequency shows -- a hand-added mutation need not
#: claim one. The character itself, not an entity: the cell is set as text by the page.
PRESENT_MARK = "✓"

VERIFIED_TITLE = "Show this position in the NCBI annotation"
UNVERIFIED_TITLE = "This sequence has not been matched to an NCBI record yet"


@dataclass(frozen=True)
class Column:
    """One descriptive column: what the row key is, what the header says, which breseq class
    styles it, and whether it shows before anyone has chosen."""
    key: str
    title: str
    css_class: str
    default_visible: bool = True


@dataclass(frozen=True)
class SampleColumn:
    id: int
    label: str
    index: int
    bam_stored: bool
    #: The sample's flags (`mutint_sample.flags.Flag`), drawn as badges in the header and menu.
    flags: tuple = ()
    #: Where the sample's header links: its own Mutations page.
    url: str = ""
    #: Which of the header palette's colors the column wears: the same for every sample of
    #: one experiment, the next for the next experiment met, wrapping after PALETTE_SIZE.
    palette: int = 0


@dataclass
class MutationMatrix:
    columns: list
    samples: list
    rows: list
    experiment_id: Optional[int] = None
    dom_id: str = "mutation-matrix"
    csv_title: str = "mutations"
    #: The mutation types the rows hold (SNP, DEL, ...), sorted, for the Types menu.
    types: tuple = ()

    @property
    def width(self):
        return len(self.columns) + len(self.samples)


#: The per-sample table's columns, minus Freq. Description is hidden until asked for: it is
#: prose, and the widest column by far.
DESCRIPTIVE = (
    Column("type", "Type", "breseq-evidence"),
    Column("seq_id", "Reference", "breseq-seq-id"),
    Column("position", "Position", "breseq-position"),
    Column("mutation", "Mutation", "breseq-mutation"),
    Column("annotation", "Annotation", "breseq-annotation"),
    Column("gene", "Gene", "breseq-gene"),
    Column("description", "Description", "breseq-description", default_visible=False),
)


#: How many colors breseq_table.css defines for `.sample-palette-<n>`.
PALETTE_SIZE = 8


def palette_indexes(experiment_ids):
    """One palette index per entry: experiments in order of first appearance, wrapping.

    A page of one experiment is all one color -- the header's own, index 0 -- and the
    cross-experiment Search page colors each experiment's columns alike.
    """
    seen = {}
    return [seen.setdefault(experiment_id, len(seen)) % PALETTE_SIZE
            for experiment_id in experiment_ids]


def experiment_id_of(sample, experiment=None):
    """The experiment given when there is one -- every sample of a per-experiment page shares
    it, and asking each sample costs a query -- and the sample's own otherwise."""
    return experiment.id if experiment is not None else sample.population.experiment_id


def sample_page_url(sample, experiment=None):
    """The sample's own Mutations page."""
    return "/mutations/breseq?experiment_id=%d&sample_id=%d" % (
        experiment_id_of(sample, experiment), sample.id)


def browse_url_for(reseq_dict):
    """The default `browse_url`: into the genome browser, when the sample has an alignment.

    Only the breseq-folder importer stores a BAM, so a bare .gd sample has none and gets no
    link -- linking would send the reader to a page explaining the absence.
    """
    def url(call):
        reseq = reseq_dict.get(call.sample_id)
        if reseq is None or not reseq.bam_stored:
            return None
        return "%s?mutation_call_id=%d" % (reverse("browse_mutation"), call.id)
    return url


def refseq_url_for(experiment=None):
    """The default `refseq_url`: the contig's page in the NCBI viewer, for every contig.

    Verified or not -- an unverified contig's page is where its accession gets recorded, so
    gating the link on verification would hide the one page that does the verifying. What
    verification changes is the title. Resolved once per experiment: Search spans many, so the
    verified set is looked up by the mutation's experiment and cached.
    """
    verified = {}
    if experiment is not None:
        verified[experiment.id] = verified_contig_names(experiment)

    def url(mutation):
        if not mutation.seq_id:
            return None, None
        experiment_id = mutation.experiment_id
        if experiment_id not in verified:
            from mutint_experiment.models import Experiment
            verified[experiment_id] = verified_contig_names(
                Experiment.objects.filter(pk=experiment_id).first())
        title = VERIFIED_TITLE if mutation.seq_id in verified[experiment_id] else UNVERIFIED_TITLE
        return "%s?mutation_id=%d" % (reverse("ncbi_view"), mutation.id), title
    return url


def _sample_cell(call, browse_url):
    text, polymorphic = _frequency(call)
    cell = {"f": text or PRESENT_MARK,
            "s": float(call.frequency) if call.frequency is not None else 1.0,
            "p": polymorphic}
    url = browse_url(call) if browse_url else None
    if url:
        cell["u"] = url
    return cell


def build_matrix(mutation_calls, reseq_dict, *, experiment=None, labels="plain",
                 browse_url=None, refseq_url=None, csv_title="mutations",
                 dom_id="mutation-matrix"):
    """Lay `mutation_calls` out against the samples in `reseq_dict`.

    `reseq_dict` is `{sample_id: Sample}` in the order the columns should appear -- what
    `get_reseq_ordered_dict` and `get_ordered_reseq_dict` return. `labels="qualified"` puts the
    experiment's name in front of each sample's, for a page that spans experiments.

    A row is one mutation, and it is included when at least one listed sample carries it
    (`present is True`); a call in a sample that is not a column is ignored, and a call that
    says "looked for and absent" leaves its cell blank. Rows come out ordered by reference
    then position, the order breseq's own report uses, and that is the order they are shown
    in: the table does not sort.

    `browse_url(call)` and `refseq_url(mutation)` -> `(url, title)` may be replaced; the
    defaults are `browse_url_for` and `refseq_url_for`.
    """
    palette = palette_indexes(experiment_id_of(s, experiment) for s in reseq_dict.values())
    samples = [SampleColumn(id=sample.id,
                            label=sample.qualified_label if labels == "qualified" else sample.label,
                            index=index, bam_stored=bool(sample.bam_stored),
                            flags=tuple(flags_of(sample)),
                            url=sample_page_url(sample, experiment), palette=palette[index])
               for index, sample in enumerate(reseq_dict.values())]
    column_of = {sample.id: sample.index for sample in samples}
    browse_url = browse_url or browse_url_for(reseq_dict)
    refseq_url = refseq_url or refseq_url_for(experiment)

    by_mutation = {}
    for call in mutation_calls:
        if call.present is not True or call.sample_id not in column_of:
            continue
        row = by_mutation.get(call.mutation_id)
        if row is None:
            row = _describe(call.mutation, refseq_url, len(samples))
            by_mutation[call.mutation_id] = row
        row["samples"][column_of[call.sample_id]] = _sample_cell(call, browse_url)

    rows = sorted(by_mutation.values(), key=lambda r: (r["seq_id_text"], r["position_sort"]))
    return MutationMatrix(columns=list(DESCRIPTIVE), samples=samples, rows=rows,
                          experiment_id=experiment.id if experiment is not None else None,
                          dom_id=dom_id, csv_title=csv_title,
                          types=tuple(sorted({row["type"] for row in rows if row["type"]})))


def _describe(mutation, refseq_url, width):
    row = describe_mutation(mutation)
    url, title = refseq_url(mutation) if refseq_url else (None, None)
    row.update({
        "id": mutation.id,
        "type": mutation.mutation_type or "",
        "seq_id_text": mutation.seq_id or "",
        "seq_id_url": url,
        "seq_id_title": title,
        "position_sort": mutation.start_position,
        "samples": [None] * width,
    })
    return row

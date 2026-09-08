"""The mutation matrix: mutations down, samples across, drawn the way breseq draws a mutation.

This is what Compare and Search render, and what a plugin renders when it has "a set of
mutation calls" to show. It replaced a builder that
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
    #: one population (ALE), the next for the next population met, wrapping after PALETTE_SIZE.
    palette: int = 0


@dataclass(frozen=True)
class RowSet:
    """A named subset of the rows, for the Show menu: `key` is what the client filters on,
    `label` what the menu says, `mutation_ids` which mutations belong.

    The matrix annotates each row with the keys of the sets holding it and offers the sets
    in a menu; it does not filter -- the reader chooses, in the browser, with the count and
    the pager describing what is left. What a set *means* is the caller's business: the
    builder is handed ids and asks nothing about how they were chosen.
    """
    key: str
    label: str
    mutation_ids: frozenset

    @property
    def count(self):
        return len(self.mutation_ids)


#: The References menu's choice, per experiment: `<prefix><experiment_id>` -> `{"hidden": [...]}`.
#: The matrix owns the `mutation_matrix.` namespace; the per-sample Mutations page reads and
#: writes this one key too, so hiding a contig on either page hides it on both.
REFERENCES_PREFERENCE_PREFIX = "mutation_matrix.references."


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
    #: The reference sequences the rows are on, sorted, for the References menu.
    seq_ids: tuple = ()
    #: The `RowSet`s the Show menu offers, each with the count of rows it holds. Empty for a
    #: page with nothing to offer, and then the menu is not rendered.
    sets: tuple = ()

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


def palette_indexes(population_ids):
    """One palette index per entry: populations in order of first appearance, wrapping.

    The first population met wears the header's own blue, index 0, so an experiment of one
    ALE is uniform and one of several reads as bands. Population ids are unique across
    experiments, so the cross-experiment Search page needs no other rule.
    """
    seen = {}
    return [seen.setdefault(population_id, len(seen)) % PALETTE_SIZE
            for population_id in population_ids]


def experiment_id_of(sample, experiment=None):
    """The experiment given when there is one -- every sample of a per-experiment page shares
    it, and asking each sample costs a query -- and the sample's own otherwise."""
    return experiment.id if experiment is not None else sample.population.experiment_id


def sample_page_url(sample, experiment=None):
    """The sample's own Mutations page."""
    return "/mutations/breseq?experiment_id=%d&sample_id=%d" % (
        experiment_id_of(sample, experiment), sample.id)


def browse_url_for(sample_dict):
    """The default `browse_url`: into the genome browser, when the sample has an alignment.

    Only the breseq-folder importer stores a BAM, so a bare .gd sample has none and gets no
    link -- linking would send the reader to a page explaining the absence.
    """
    def url(call):
        sample = sample_dict.get(call.sample_id)
        if sample is None or not sample.bam_stored:
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


def build_matrix(mutation_calls, sample_dict, *, experiment=None, labels="plain",
                 browse_url=None, refseq_url=None, csv_title="mutations",
                 dom_id="mutation-matrix", sets=(), ancestral_mutation_ids=frozenset()):
    """Lay `mutation_calls` out against the samples in `sample_dict`.

    `sample_dict` is `{sample_id: Sample}` in the order the columns should appear -- what
    `get_ordered_sample_dict` and `samples_in_calls` return. `labels="qualified"` puts the
    experiment's name in front of each sample's, for a page that spans experiments.

    A row is one mutation, and it is included when at least one listed sample carries it
    (`present is True`); a call in a sample that is not a column is ignored, and a call that
    says "looked for and absent" leaves its cell blank. Rows come out ordered by reference
    then position, the order breseq's own report uses, and that is the order they are shown
    in: the table does not sort.

    `browse_url(call)` and `refseq_url(mutation)` -> `(url, title)` may be replaced; the
    defaults are `browse_url_for` and `refseq_url_for`.

    `sets` is a sequence of `RowSet`s. Each row is annotated with the keys of the sets that
    hold its mutation, and the sets are offered in the Show menu, counted by the rows they
    hold here rather than by the ids handed in: an id no listed sample carries is no row.

    `ancestral_mutation_ids` marks the rows observed in the experiment's designated ancestor,
    for the script to tint the way the per-sample table tints them -- the same argument
    `build_rows` takes, for the same reason. Display only: the matrix filters nothing and
    asks nothing about why a row is ancestral. A page that wants those rows drawn hands the
    raw calls in and the ids alongside; one that does not hands the evolved calls in and
    nothing here changes.
    """
    palette = palette_indexes(s.population_id for s in sample_dict.values())
    samples = [SampleColumn(id=sample.id,
                            label=sample.qualified_label if labels == "qualified" else sample.label,
                            index=index, bam_stored=bool(sample.bam_stored),
                            flags=tuple(flags_of(sample)),
                            url=sample_page_url(sample, experiment), palette=palette[index])
               for index, sample in enumerate(sample_dict.values())]
    column_of = {sample.id: sample.index for sample in samples}
    browse_url = browse_url or browse_url_for(sample_dict)
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
    if sets:
        for row in rows:
            row["sets"] = [s.key for s in sets if row["id"] in s.mutation_ids]
        sets = tuple(RowSet(s.key, s.label, frozenset(s.mutation_ids & by_mutation.keys()))
                     for s in sets)
    if ancestral_mutation_ids:
        for row in rows:
            row["ancestral"] = row["id"] in ancestral_mutation_ids
    return MutationMatrix(columns=list(DESCRIPTIVE), samples=samples, rows=rows,
                          experiment_id=experiment.id if experiment is not None else None,
                          dom_id=dom_id, csv_title=csv_title,
                          types=tuple(sorted({row["type"] for row in rows if row["type"]})),
                          seq_ids=tuple(sorted({row["seq_id_text"] for row in rows if row["seq_id_text"]})),
                          sets=sets)


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

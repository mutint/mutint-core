"""igv.js tracks built from the database rather than from a file.

Every track the genome browser drew came from the managed store -- the reference FASTA, its
GFF3 gene track, a sample's BAM, its coverage BigWig. The mutations themselves, which are the
reason anybody opens the page, were not drawn at all: `browse.html` passed `tracks: []`, and
the only thing the database contributed to igv was the locus string positioning the view.

igv.js takes features as an inline array -- no file, no route, no `store.py` whitelist slot and
no `EXTENSION_CONTENT_TYPES` entry -- so this module is the whole of what was missing. It is
pure, in the shape `locus.py` and `functional_change.py` are: no `django.http`, no template,
querysets in and dictionaries out.

**The reference gene track stays what it was**, and is why drawing here rather than only on the
NCBI page matters: it is *this experiment's* annotation, rendered by `aledb_import.annotate.gff3`
from whatever GenBank or GFF3 was imported. NCBI's copy of the same genome may annotate it
differently, and this is the view that shows what the data were actually called against.

**Coordinates.** igv features are **0-based, end-exclusive**; GenomeDiff positions and
`locus.mutation_extent()` are **1-based inclusive**. So `start = start_1 - 1` and `end = end_1`.
Getting this wrong shifts every feature one base and looks entirely plausible -- the track
draws, the mutations simply sit beside the gene they are in.
"""

from aledb_common.util import get_gene_list
from aledb_experiment.ordering import sample_order
from aledb_sample.functional_change import UNANNOTATED, functional_change_bucket

#: Colour per functional-change bucket, reusing `functional_change`'s vocabulary rather than
#: keeping a second opinion about severity. In that list's order, which *is* the severity
#: hierarchy, so the palette darkens with consequence rather than by chance.
BUCKET_COLOURS = {
    "nonsense": "rgb(165, 15, 21)",
    "nonsynonymous": "rgb(222, 45, 38)",
    "synonymous": "rgb(49, 130, 189)",
    "noncoding": "rgb(107, 174, 214)",
    "pseudogene": "rgb(117, 107, 177)",
    "intergenic": "rgb(150, 150, 150)",
    UNANNOTATED: "rgb(99, 99, 99)",
}

#: Every feature ships inline in the page, so this bounds the page rather than the query. The
#: largest experiment in the dev database is 5,076 mutations, comfortably inside it; the cap is
#: here so a far larger one degrades to a partial track instead of a page nobody can load.
MAX_FEATURES = 20000

#: igv track ids, so the page can find a track without matching on the name it displays.
MUTATION_TRACK_ID = "aledb-mutations"
SAMPLE_TRACK_ID = "aledb-mutations-by-sample"

#: Every `seg` feature is drawn with the same value, so the track marks **presence, not
#: magnitude**, and the frequency rides along for igv's popup instead.
#:
#: This was going to encode frequency as colour, and three browser probes said not to. igv's
#: seg scale is diverging around zero and was built for log2 copy ratios: handed raw
#: frequencies in [0, 1] it paints 5% and 100% the identical blue. Mapping them into [0.35,
#: 1.5] to spread them out then rendered *lighter* as frequency rose, while a symmetric
#: [-1.5, 1.5] track rendered *darker* toward both ends -- which only makes sense if the
#: track autoscales to whatever range it was given.
#:
#: That is the disqualifying property, rather than any particular direction being wrong: a
#: colour that depends on the rest of the track means the same frequency looks different on
#: two experiments' pages, and nobody can learn to read it. A uniform mark that says "called
#: here, in this sample" is a smaller claim and a true one.
SEG_PRESENT = -1.0

#: Whether `database_tracks` offers the per-sample seg track it can build.
#:
#: Off. The Mutations track answers "what was called here"; this one laid the same calls out
#: again as a band per sample, and in use it was not worth the vertical space it took beneath
#: them -- the sample menu's `*` already says which samples carry the mutation being looked at.
#:
#: A switch rather than a deletion, because what was decided is that the track does not earn
#: its place, not that it is wrong: `sample_features` is unchanged and still tested directly,
#: so turning this back on restores a working track rather than resurrecting rotted code.
#: `browse.html`'s `showSampleNames` is the other half of it -- seg rows draw unlabelled
#: without it -- and is kept for the same reason.
DRAW_SAMPLE_TRACK = False


def _interval(start_position, end_position):
    """`(start, end)` for igv: 0-based, end-exclusive.

    `start_position` is always set -- it is the record's own position, written at creation --
    so only the extent can be missing. A mutation imported before a reference was available
    has no `end_position` and is drawn as a point.

    It took a third argument, `position`, which was the same number as `start_position` in
    every row: the two columns merged. Deliberately still not a call into `mutation_extent`:
    that takes a model instance and this reads tuples, and re-deriving from `gd_data` here
    would mean fetching a JSONField for every row to answer what two integer columns say.
    """
    end_1 = end_position or start_position
    return max(0, start_position - 1), end_1


def _label(mutation_type, sequence_change, gene):
    """What igv shows on the feature and in its popup.

    The gene column can run to 19,000 characters for a mutation spanning thousands of genes,
    so it is never used whole -- `get_gene_list` is the one parser that handles both
    separators the column is written with, and past the first name this says how many more
    there are rather than listing them.
    """
    parts = [mutation_type or "?"]
    if sequence_change:
        parts.append(str(sequence_change))
    names = [name for name in get_gene_list(gene or "") if name and name != "None"]
    if len(names) == 1:
        parts.append(names[0])
    elif len(names) > 1:
        parts.append("%s +%d more" % (names[0], len(names) - 1))
    return " ".join(parts)


def mutation_features(experiment_id, contig=None):
    """This experiment's `Mutation` rows as igv annotation features.

    One feature per mutation rather than per call: this track answers "what was called
    anywhere in this experiment", and `frequency_features` is the per-sample view.

    Read as `values_list` tuples rather than model instances, the `get_needle_plot_data`
    pattern, and for the same reason: a `Mutation` carries two JSONFields and a gene column of
    up to 19,000 characters, and this needs eight columns.

    **Reached through the calls, not through `Mutation.experiment`**, so this track
    and `frequency_features` can never disagree about what belongs to the experiment. Filtering
    on the column looks equivalent and is not: a `Mutation` may carry a null `experiment`
    -- the unscoped case `permissions.can_curate` exists to handle -- and two such rows in the
    dev database were observed in an experiment while being owned by none, so the Mutations
    track came up empty beside a Frequency track with features in it.

    Ancestor-subtracted as a consequence, and correctly: an ancestral mutation is in every
    sample by construction, so it belongs on neither track. Both now answer the same question,
    "what evolved in this experiment", rather than two subtly different ones.
    """
    from aledb_sample.models import Mutation
    from aledb_sample.util import get_evolved_call_queryset

    calls = get_evolved_call_queryset(experiment_id)
    if contig:
        calls = calls.filter(mutation__seq_id=contig)

    rows = (Mutation.objects
            .filter(id__in=calls.values("mutation_id"))
            .exclude(seq_id__isnull=True)
            .order_by("seq_id", "start_position"))
    if contig:
        rows = rows.filter(seq_id=contig)

    features = []
    for (pk, seq_id, start_position, end_position, mutation_type,
         sequence_change, snp_type, gene) in rows.values_list(
            "id", "seq_id", "start_position", "end_position",
            "mutation_type", "sequence_change", "snp_type", "gene"
    ).iterator(chunk_size=2000):
        start, end = _interval(start_position, end_position)
        bucket = functional_change_bucket(snp_type)
        features.append({
            "chr": seq_id,
            "start": start,
            "end": end,
            "name": _label(mutation_type, sequence_change, gene),
            "color": BUCKET_COLOURS.get(bucket, BUCKET_COLOURS[UNANNOTATED]),
            # Read back by the page to link a clicked feature at the mutation editor and the
            # NCBI view, which both address a mutation by primary key.
            "mutationId": pk,
        })
        if len(features) >= MAX_FEATURES:
            break
    return features


def sample_features(experiment_id, contig=None):
    """Which samples carry which mutation, as igv `seg` features -- one row per sample.

    `seg` is igv's sample-row track: it keys rows on `sample`, which is what lays an
    experiment out as mutations across samples on a genome axis. The colour says only
    "present"; see `SEG_PRESENT` for why the frequency is not in it.

    **Ancestor-subtracted**, through `get_evolved_call_queryset`. An ancestral mutation
    is in every sample by construction, so drawing it would paint a band across the whole track
    that says nothing about what evolved -- the same reason convergence and fixation subtract.

    Rows are labelled and ordered by `get_ordered_reseq_queryset`, the same helper every
    sample listing uses, so the track reads in the order of the tables beside it and calls
    each sample what they call it.

    **The label cannot come out of `values_list`.** `label` is a property that
    falls back through the isolate's description to a computed `A# F# I# R#`, so pulling
    `...isolate__description` instead -- which is what this did first -- yields NULL for every
    sample that has none, and every row in the experiment collapses onto one track row named
    "sample". One small query for the samples and a dict is the fix; there are tens of them,
    not thousands.
    """
    from aledb_sample.util import get_evolved_call_queryset, get_ordered_reseq_queryset

    labels = {reseq.id: reseq.label
              for reseq in get_ordered_reseq_queryset(experiment_id)}

    rows = get_evolved_call_queryset(experiment_id).filter(present=True)
    if contig:
        rows = rows.filter(mutation__seq_id=contig)

    features = []
    for (seq_id, start_position, end_position, frequency,
         sample_id) in rows.order_by(*sample_order("sample__")).values_list(
            "mutation__seq_id", "mutation__start_position",
            "mutation__end_position", "frequency", "sample_id",
    ).iterator(chunk_size=2000):
        if not seq_id or sample_id not in labels:
            # Not in the ordered list means filtered out of it -- the ancestor, or a sample
            # type the listing excludes. Drawing it would put a row on the track that the
            # tables beside it do not show.
            continue
        start, end = _interval(start_position, end_position)
        features.append({
            "chr": seq_id,
            "start": start,
            "end": end,
            "value": SEG_PRESENT,
            # Not encoded in the colour -- see SEG_PRESENT -- but carried so igv's popup can
            # show the number, which is exact where a colour would only be suggestive.
            "frequency": None if frequency is None else float(frequency),
            "sample": labels[sample_id],
        })
        if len(features) >= MAX_FEATURES:
            break
    return features


def database_tracks(experiment_id, contig=None):
    """Both tracks, as igv track configs, or `[]` when there is nothing to draw.

    An empty track is worse than no track: igv draws its name and an empty lane, which reads
    as "there are no mutations here" rather than "this experiment has none". So each is
    included only when it has features.
    """
    tracks = []
    mutations = mutation_features(experiment_id, contig)
    if mutations:
        tracks.append({
            # Stable across a rename of the label. The page reads a clicked feature off this
            # track to switch which mutation it is about, and matching on the display name
            # would make rewording it silently break the click.
            "id": MUTATION_TRACK_ID,
            "name": "Mutations",
            "type": "annotation",
            "displayMode": "EXPANDED",
            "height": 70,
            "order": 1,
            "features": mutations,
        })

    if DRAW_SAMPLE_TRACK:
        per_sample = sample_features(experiment_id, contig)
        if per_sample:
            tracks.append({
                "id": SAMPLE_TRACK_ID,
                "name": "Mutations by sample",
                "type": "seg",
                "displayMode": "EXPANDED",
                "order": 2,
                "features": per_sample,
            })
    return tracks

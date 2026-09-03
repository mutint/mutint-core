from aledb_dashboard.models import ObservedMutationCounts, UniqueMutationCounts, SampleCounts
from aledb_seq.models import ObservedMutation
from aledb_seq.functional_change import (
    FUNCTIONAL_CHANGE_TYPE_LIST, functional_change_bucket,
)
from aledb_seq.views.common import MUTATION_TYPE_LIST, UNANNOTATED
from aledb_experiment.ancestor import (exclude_all_ancestry,
                                       exclude_ancestor_samples)
from aledb_experiment.models import AleExperiment, AleId, Isolate, Flask
from django.db.models import Q
from aledb_experiment import paths


def rebuild_dashboard_data():
    rebuild_sample_counts()
    rebuild_mutation_counts()


#: The join from an ObservedMutation up to its experiment, as `aledb_seq.util`,
#: `aledb_filter.util` and `aledb_mutation_editor.history` all spell it.
_EXPERIMENT_PATH = paths.to_experiment(paths.FROM_OBSERVATION)

#: Deletion here is soft: it sets `deleted_at` and leaves everything below the experiment in
#: place, and this app's managers are deliberately unfiltered -- so these totals counted every
#: project and experiment anybody had ever removed. Both halves are needed: deleting a project
#: does not stamp its experiments.


def _evolved_samples():
    from aledb_seq.models import ResequencingExperiment
    return exclude_ancestor_samples(ResequencingExperiment.objects.all())


def _purely_ancestral(model, sample_path):
    """Rows of `model` whose every sample is a designated ancestor.

    `STARTING_STRAIN_ALE_ID` stood here: three `~Q(ale_id="0")` clauses approximating "do not
    count the ancestor" from an ALE label. This asks the real question instead, and an ALE
    genuinely labelled "0" is counted like any other.

    **It still counts rows, not samples.** An AleId, Flask or Isolate carrying no samples at
    all has always been counted here and still is -- dropping those would move the published
    totals for a reason that has nothing to do with ancestors. What is dropped is only a row
    that has samples and whose samples are *all* ancestral, which is the case the ALE-0 rule
    was really reaching for. A flask holding the ancestor alongside other samples still counts.

    Two of the three original clauses were also redundant: the flask and isolate counts
    already filtered `ale_id__in=live_ales`, which had excluded ALE 0 once over.
    """
    ancestors = AleExperiment.objects.filter(ancestor__isnull=False).values("ancestor")
    if not ancestors.exists():
        return model.objects.none()
    return (model.objects.filter(**{"%s__in" % sample_path: ancestors})
            .exclude(**{"%s__in" % sample_path:
                        _evolved_samples().order_by().values("pk")}))


#: Where a sample sits, seen from each of the three rows counted below.
_SAMPLE_FROM_ALE = "flask__isolate__technicalreplicate__resequencingexperiment"
_SAMPLE_FROM_FLASK = "isolate__technicalreplicate__resequencingexperiment"
_SAMPLE_FROM_ISOLATE = "technicalreplicate__resequencingexperiment"


def rebuild_sample_counts():
    if SampleCounts.objects.all().count() == 0:
        SampleCounts.objects.create()
    live_ales = AleId.objects.filter(
        Q(ale_experiment__deleted_at__isnull=True)
        & Q(ale_experiment__project__deleted_at__isnull=True))

    ale_count = live_ales.exclude(
        pk__in=_purely_ancestral(AleId, _SAMPLE_FROM_ALE)).distinct().count()
    flask_count = Flask.objects.filter(ale_id__in=live_ales).exclude(
        pk__in=_purely_ancestral(Flask, _SAMPLE_FROM_FLASK)).distinct().count()
    isolate_count = Isolate.objects.filter(flask__ale_id__in=live_ales).exclude(
        pk__in=_purely_ancestral(Isolate, _SAMPLE_FROM_ISOLATE)).distinct().count()

    SampleCounts.objects.all().update(ale_count=ale_count, flask_count=flask_count,
                                      isolate_count=isolate_count)


def _live_observation_rows():
    """Every observation the installation still has, as `(mutation_id, type, snp_type)`.

    **The dashboard applies no filter, deliberately.** It is an inventory of what the
    installation holds, and an experiment's frequency cutoff or ignored-gene list is one
    person's view of one experiment -- a site-wide total computed through it answers a question
    nobody asked, and could not be computed at all once filtering is per-user, since a shared
    table cannot be keyed by user. It used to call `filter_observed_mutations`, which in the
    dev database made the stored total 73,857 where the installation holds 74,859.

    Tuples rather than model instances, and that is what the filter's removal buys: filtering
    needed the gene column parsed per row, so the rows had to be built -- every one of them
    joined across six tables and carrying two JSONFields. Counting needs three columns.
    `rebuild_after_structural_change` refuses to run this rebuild at all because it "pulls
    every ObservedMutation in the database into Python", and that is the sentence this is
    meant to stop being true.

    The third column is `snp_type`, breseq's own functional class, and used to be
    `protein_change` -- a rendered display string that contains none of the words being looked
    for. See `aledb_seq.functional_change`.
    """
    queryset = ObservedMutation.objects.filter(
        **{"%s__deleted_at__isnull" % _EXPERIMENT_PATH: True,
           "%s__project__deleted_at__isnull" % _EXPERIMENT_PATH: True})

    # The designated ancestors *are* subtracted, unlike the reader's filter above. A frequency
    # cutoff is one person's view of one experiment; an ancestor is a fact about the dataset,
    # and counting the starting line as evolution inflates the headline number this page
    # exists to report. One exclusion for every experiment at once -- see
    # `exclude_all_ancestry` for why that is safe rather than merely cheap.
    queryset = exclude_all_ancestry(queryset)

    return queryset.values_list("mutation_id", "mutation__mutation_type",
                                "mutation__snp_type").iterator(chunk_size=2000)


def rebuild_mutation_counts():
    mut_count_dict = {mut_type: 0 for mut_type in MUTATION_TYPE_LIST}
    mut_func_change_type_dict = {t: 0 for t in FUNCTIONAL_CHANGE_TYPE_LIST}
    obs_mut_count_dict = {mut_type: 0 for mut_type in MUTATION_TYPE_LIST}
    obs_mut_func_change_type_dict = {t: 0 for t in FUNCTIONAL_CHANGE_TYPE_LIST}

    # "Unique" is distinct mutations, so each id is bucketed the first time it is seen. This
    # was a `{id: mutation}` dict of model instances (`get_mutations_from_observed_muations`);
    # a set of ids is the same answer without the rows.
    seen = set()
    observed_total = 0
    for mutation_id, mutation_type, snp_type in _live_observation_rows():
        observed_total += 1
        bucket = _mutation_type_bucket(mutation_type)
        change = functional_change_bucket(snp_type)
        obs_mut_count_dict[bucket] += 1
        obs_mut_func_change_type_dict[change] += 1
        if mutation_id not in seen:
            seen.add(mutation_id)
            mut_count_dict[bucket] += 1
            mut_func_change_type_dict[change] += 1

    if ObservedMutationCounts.objects.all().count() == 0:
        ObservedMutationCounts.objects.create()
    obs_mut_count_qryset = ObservedMutationCounts.objects.all()
    if UniqueMutationCounts.objects.all().count() == 0:
        UniqueMutationCounts.objects.create()
    mut_count_qryset = UniqueMutationCounts.objects.all()

    obs_mut_count_qryset.update(total=observed_total)
    mut_count_qryset.update(total=len(seen))

    for mutation_type in MUTATION_TYPE_LIST:
        observed_mutation_type_count = obs_mut_count_dict[mutation_type]
        unique_mutation_type_count = mut_count_dict[mutation_type]
        if mutation_type == 'SNP':
            obs_mut_count_qryset.update(single_base_substitution=observed_mutation_type_count)
            mut_count_qryset.update(single_base_substitution=unique_mutation_type_count)
        elif mutation_type == 'SUB':
            obs_mut_count_qryset.update(multiple_base_substitution=observed_mutation_type_count)
            mut_count_qryset.update(multiple_base_substitution=unique_mutation_type_count)
        elif mutation_type == 'DEL':
            obs_mut_count_qryset.update(deletion=observed_mutation_type_count)
            mut_count_qryset.update(deletion=unique_mutation_type_count)
        elif mutation_type == 'INS':
            obs_mut_count_qryset.update(insertion=observed_mutation_type_count)
            mut_count_qryset.update(insertion=unique_mutation_type_count)
        elif mutation_type == 'MOB':
            obs_mut_count_qryset.update(mobile_element_insertion=observed_mutation_type_count)
            mut_count_qryset.update(mobile_element_insertion=unique_mutation_type_count)
        elif mutation_type == 'AMP':
            obs_mut_count_qryset.update(amplification=observed_mutation_type_count)
            mut_count_qryset.update(amplification=unique_mutation_type_count)
        elif mutation_type == 'CON':
            obs_mut_count_qryset.update(gene_conversion=observed_mutation_type_count)
            mut_count_qryset.update(gene_conversion=unique_mutation_type_count)
        elif mutation_type == 'INV':
            obs_mut_count_qryset.update(inversion=observed_mutation_type_count)
            mut_count_qryset.update(inversion=unique_mutation_type_count)

    # The two totals above deliberately do not equal the sum of these columns: a mutation
    # whose type is not in MUTATION_TYPE_LIST is bucketed UNANNOTATED, which has no column
    # here. That difference used to be reported by a `print` on every rebuild, including
    # every test run.

    # Every token in FUNCTIONAL_CHANGE_TYPE_LIST is spelled identically to its column on both
    # count models, so the twenty-two-line if/elif chain that stood here is a dict comprehension
    # and adding `nonsense` needed no code at all. That equality is the invariant: a token added
    # to the vocabulary without its migration raises FieldError on the next rebuild rather than
    # being silently dropped, which is the behaviour to want -- a bucket counted into nothing is
    # exactly how `synonymous` and `nonsynonymous` sat at zero for years.
    #
    # The mutation-type chain above is left alone: 'SNP' -> single_base_substitution is a real
    # mapping between two different vocabularies, not an identity.
    obs_mut_count_qryset.update(**{change: obs_mut_func_change_type_dict[change]
                                   for change in FUNCTIONAL_CHANGE_TYPE_LIST})
    mut_count_qryset.update(**{change: mut_func_change_type_dict[change]
                               for change in FUNCTIONAL_CHANGE_TYPE_LIST})


def _mutation_type_bucket(mutation_type):
    """The type, or UNANNOTATED for one outside the vocabulary.

    Bucketed, unlike the Overview, which drops such a mutation from every type count.
    """
    if mutation_type in MUTATION_TYPE_LIST:
        return mutation_type
    return UNANNOTATED

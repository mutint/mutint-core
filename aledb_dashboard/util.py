from aledb_dashboard.models import InstallationCounts
from aledb_sample.models import MutationCall
from aledb_sample.functional_change import (
    FUNCTIONAL_CHANGE_TYPE_LIST, functional_change_bucket,
)
from aledb_sample.views.common import MUTATION_TYPE_LIST, UNANNOTATED
from aledb_experiment.ancestor import (exclude_all_ancestry,
                                       exclude_ancestor_samples)
from aledb_experiment.models import Experiment, Population
from django.db.models import Q
from aledb_experiment import paths


def counts(name):
    """One row's payload, or `{}` if it has never been built.

    Every reader goes through this. It answers a plain dict so no caller has to hold a model
    instance or guard on one -- the two views each carried their own
    `if unique_mutation_counts and mutation_call_counts:` dance around exactly that.
    """
    return (InstallationCounts.objects
            .filter(name=name).values_list("data", flat=True).first()) or {}


#: The join from a MutationCall up to its experiment, as `aledb_sample.util`,
#: `aledb_filter.util` and `aledb_mutation_editor.history` all spell it.
_EXPERIMENT_PATH = paths.to_experiment(paths.FROM_CALL)


def _evolved_samples():
    from aledb_sample.models import Sample
    return exclude_ancestor_samples(Sample.objects.all())


def _purely_ancestral(model, sample_path):
    """Rows of `model` whose every sample is a designated ancestor.

    `STARTING_STRAIN_ALE_ID` stood here: three `~Q(ale_id="0")` clauses approximating "do not
    count the ancestor" from an ALE label. This asks the real question instead, and an ALE
    genuinely labeled "0" is counted like any other.

    **It still counts rows, not samples.** An Population or TimePoint carrying no samples at
    all has always been counted here and still is -- dropping those would move the published
    totals for a reason that has nothing to do with ancestors. What is dropped is only a row
    that has samples and whose samples are *all* ancestral, which is the case the ALE-0 rule
    was really reaching for. A flask holding the ancestor alongside other samples still counts.

    Two of the three original clauses were also redundant: the flask and sample counts
    already filtered `population__in=live_ales`, which had excluded ALE 0 once over.
    """
    ancestors = Experiment.objects.filter(ancestor__isnull=False).values("ancestor")
    if not ancestors.exists():
        return model.objects.none()
    return (model.objects.filter(**{"%s__in" % sample_path: ancestors})
            .exclude(**{"%s__in" % sample_path:
                        _evolved_samples().order_by().values("pk")}))


#: Where a sample sits, seen from the one row still counted below that is not the sample
#: itself. The other two *are* samples now -- `Isolate` was folded into `Sample`, and
#: `TimePoint` became a column on it -- so they ask their question directly rather than
#: through `_purely_ancestral`.
_SAMPLE_FROM_POPULATION = paths.down_chain("population")


def rebuild_sample_counts():
    """The `inventory` row: how many populations, time points and samples are live.

    Soft deletion is why `live_populations` filters both `deleted_at` columns. Removing an
    experiment sets its own flag and leaves everything below it in place, and this app's
    managers are deliberately unfiltered -- so these totals used to count every project and
    experiment anybody had ever removed. Both halves are needed: deleting a *project* does
    not stamp its experiments.
    """
    live_populations = Population.objects.filter(
        Q(experiment__deleted_at__isnull=True)
        & Q(experiment__project__deleted_at__isnull=True))

    population_count = live_populations.exclude(
        pk__in=_purely_ancestral(Population, _SAMPLE_FROM_POPULATION)).distinct().count()
    # Distinct (population, time point) pairs, where this counted `TimePoint` rows. It is
    # the same number for the same reason the sample count is: that row existed once per
    # pair, and only the import ever made one. What it cannot do any more is count a time
    # point that no sample sits at -- and such a row was unreachable from every listing
    # anyway, so it was never a number anybody could act on.
    #
    # `_evolved_samples` rather than `_purely_ancestral`: with the row gone, "a time point
    # whose every sample is ancestral" is just a pair no evolved sample holds.
    time_point_count = (_evolved_samples()
                        .filter(**{paths.to_population() + "__in": live_populations})
                        .exclude(**{paths.to_time_point_value() + "__isnull": True})
                        .values(paths.to_population(), paths.to_time_point_value())
                        .distinct().count())
    # The third number counted `Isolate` rows and counts samples now, which is the same
    # number: a sample was one run under one replicate under one isolate, and only the
    # import created any of them. What changes is that "a row whose every sample is
    # ancestral" is simply "an ancestral sample", so this drops out of `_purely_ancestral`
    # and excludes the designated ancestors directly.
    sample_count = (_evolved_samples()
                    .filter(**{paths.to_population() + "__in": live_populations})
                    .distinct().count())

    # One upsert where this was a create-if-empty followed by a blind `.all().update()` --
    # which wrote every row, the table having had no way to say it held only one.
    InstallationCounts.objects.update_or_create(
        name=InstallationCounts.INVENTORY,
        defaults={"data": {"population": population_count,
                           "time_point": time_point_count,
                           "sample": sample_count}})


def _live_call_rows():
    """Every call the installation still has, as `(mutation_id, type, snp_type)`.

    **The dashboard applies no filter, deliberately.** It is an inventory of what the
    installation holds, and an experiment's frequency cutoff or ignored-gene list is one
    person's view of one experiment -- a site-wide total computed through it answers a question
    nobody asked, and could not be computed at all once filtering is per-user, since a shared
    table cannot be keyed by user. It used to call `filter_mutation_calls`, which in the
    dev database made the stored total 73,857 where the installation holds 74,859.

    Tuples rather than model instances, and that is what the filter's removal buys: filtering
    needed the gene column parsed per row, so the rows had to be built -- every one of them
    joined across six tables and carrying two JSONFields. Counting needs three columns.
    `rebuild_after_structural_change` refuses to run this rebuild at all because it "pulls
    every MutationCall in the database into Python", and that is the sentence this is
    meant to stop being true.

    The third column is `snp_type`, breseq's own functional class, and used to be
    `protein_change` -- a rendered display string that contains none of the words being looked
    for. See `aledb_sample.functional_change`.
    """
    queryset = MutationCall.objects.filter(
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
    call_count_dict = {mut_type: 0 for mut_type in MUTATION_TYPE_LIST}
    call_func_change_type_dict = {t: 0 for t in FUNCTIONAL_CHANGE_TYPE_LIST}

    # "Unique" is distinct mutations, so each id is bucketed the first time it is seen. This
    # was a `{id: mutation}` dict of model instances (`get_mutations_from_calls`);
    # a set of ids is the same answer without the rows.
    seen = set()
    call_total = 0
    for mutation_id, mutation_type, snp_type in _live_call_rows():
        call_total += 1
        bucket = _mutation_type_bucket(mutation_type)
        change = functional_change_bucket(snp_type)
        call_count_dict[bucket] += 1
        call_func_change_type_dict[change] += 1
        if mutation_id not in seen:
            seen.add(mutation_id)
            mut_count_dict[bucket] += 1
            mut_func_change_type_dict[change] += 1

    # Two upserts, storing the dicts exactly as they were just built.
    #
    # **An eighteen-branch `if/elif` chain stood here**, plus roughly thirty-four one-column
    # `UPDATE` statements, translating `'SNP'` into `single_base_substitution` and so on for
    # each of two models. All of it existed to spread these four dicts across columns. Keying
    # the payload by the vocabulary token deletes the translation from the write path -- and
    # deletes a bug with it: the chain had eight branches for a nine-entry list, so the
    # `unannotated` type bucket was counted here and then dropped on the floor, which is why
    # the displayed types did not add up to the total. They do now.
    #
    # `type` and `functional_change` are nested rather than merged because both vocabularies
    # contain a token spelled `unannotated`, meaning "no type we know" and "no SNP class we
    # know" respectively. Flattened, one would silently overwrite the other.
    for name, totals, types, changes in (
            (InstallationCounts.MUTATION_CALLS, call_total,
             call_count_dict, call_func_change_type_dict),
            (InstallationCounts.UNIQUE_MUTATIONS, len(seen),
             mut_count_dict, mut_func_change_type_dict)):
        InstallationCounts.objects.update_or_create(
            name=name,
            defaults={"data": {"total": totals,
                               "type": types,
                               "functional_change": changes}})


def _mutation_type_bucket(mutation_type):
    """The type, or UNANNOTATED for one outside the vocabulary.

    Bucketed, unlike the Overview, which drops such a mutation from every type count.
    """
    if mutation_type in MUTATION_TYPE_LIST:
        return mutation_type
    return UNANNOTATED

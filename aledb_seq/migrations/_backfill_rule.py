"""How 0006 decides which experiment an unlinked mutation belongs to.

Its own module, underscore-prefixed so Django's migration loader skips it -- every
other module in a migrations package must expose a Migration class. Kept separate
from the migration so the rule can be tested against the real models, which is
where the awkward case lives.
"""

from collections import defaultdict

# The path ObservedMutation.get_experiment_id() has always walked.
CHAIN = "sequencing_experiment__tech_rep__isolate__flask__ale_id__ale_experiment_id"


def resolve(observed_model, mutation_ids):
    """Map mutation id -> experiment id, for the ones that resolve unambiguously.

    A mutation observed in samples from more than one experiment is left out.
    Mutations are meant to be per-experiment -- two experiments calling the same
    variant get their own rows, so re-annotating one cannot rewrite another's --
    and choosing one of several here would quietly attach it to the wrong one.

    One query for the whole join rather than a walk per mutation: the chain is
    five joins deep and these tables run to millions of rows on a real instance.
    """
    found = defaultdict(set)
    rows = observed_model.objects.filter(
        mutation_id__in=list(mutation_ids)).values_list("mutation_id", CHAIN)
    for mutation_id, experiment_id in rows:
        if experiment_id is not None:
            found[mutation_id].add(experiment_id)
    return {mutation_id: experiments.pop()
            for mutation_id, experiments in found.items() if len(experiments) == 1}

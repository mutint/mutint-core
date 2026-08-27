"""Bring the experiment filter's hidden mutations into the change log.

`AleExperimentFilter.ignored_mutations`, `AleExperimentFilter.starting_strain_mutations` and
`GlobalFilter.ignored_mutations` held comma-joined `Mutation.id` strings that
`filter_observed_mutations` excluded from every table. That was a way of deleting a mutation
while keeping the row. This migration turns each of them into what it always meant -- the
observations removed -- and records it, so that what was hidden stays hidden and is now also
visible on the history page and restorable from it.

**The counts do not move.** Those mutations were already excluded from every table, every
export and every derived count; afterwards their rows are gone, so they are still excluded. The
one visible difference is that "Show Experiment Filtered" no longer reveals them, because they
are no longer filtered.

One changeset per experiment rather than one per mutation: it was one decision by whoever
maintained that list, and a hundred history rows saying the same thing would bury everything
else. `created_by` is null, which the history page renders as "system".

Deliberately irreversible. The columns can be recreated empty -- `aledb_filter`'s 0003 does
that on the way back -- but which mutations were in them is not something to reconstruct, and
the change log holds the same information in a better form.
"""

from django.db import migrations

_NOTE = ("Migrated from the experiment filter's %s list, which is no longer part of "
         "filtering. These observations were already hidden everywhere; they are removed "
         "now, and can be restored from here.")


def _ids(raw):
    """The integers in a comma-joined id string, ignoring anything that is not one.

    `get_ignored_mut_id_list_from_str` did exactly this and is deleted along with the columns,
    so the parsing is repeated here rather than imported -- a migration must not depend on code
    that can change under it.
    """
    if not raw:
        return []
    found = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            found.append(int(part))
        except ValueError:
            continue
    return found


def _observation_snapshot(observed):
    """Mirrors `history.OBSERVATION_FIELDS` as it stood when this migration was written.

    Spelled out rather than imported, as a historical migration must be: `history` is live
    code and its list has already changed once since. `breseq_present` and `gatk_present`
    were dropped by `aledb_seq.0010`, and which side of that a database is on depends on
    where this migration falls in its graph -- so the columns are read with a default rather
    than assumed to exist. A snapshot taken after the drop simply has no entry for them,
    which is what `history._observation_kwargs` already tolerates: it reads the fields it
    wants out of the blob and ignores the rest.
    """
    snapshot = {}
    for field in ("present", "breseq_present", "gatk_present", "wt_reads", "mutated_reads",
                  "other_reads", "reference_genome_likelihood", "frequency",
                  "frequency_gatk", "source"):
        if not hasattr(observed, field):
            continue
        value = getattr(observed, field)
        if field in ("frequency", "frequency_gatk") and value is not None:
            value = str(value)
        snapshot[field] = value
    return snapshot


def _mutation_identity(mutation):
    """Mirrors `history.mutation_identity`."""
    identity = {field: getattr(mutation, field) for field in
                ("position", "reseq_reference", "mutation_type", "feature_length",
                 "sequence_change", "gene")}
    identity["gd_data"] = mutation.gd_data
    identity["annotation"] = mutation.annotation
    identity["product"] = mutation.product
    identity["protein_change"] = mutation.protein_change
    return identity


def _collect(apps):
    """{(experiment_id, source_label): {mutation_id, ...}} from all three columns."""
    AleExperimentFilter = apps.get_model("aledb_filter", "AleExperimentFilter")
    GlobalFilter = apps.get_model("aledb_filter", "GlobalFilter")
    Mutation = apps.get_model("aledb_seq", "Mutation")

    wanted = {}

    def note(mutation_ids, label):
        for mutation in Mutation.objects.filter(id__in=mutation_ids):
            if mutation.ale_experiment_id is None:
                # A mutation with no experiment cannot be scoped to a changeset. Nothing has
                # created one since aledb_seq.0006 backfilled the column, and leaving it in
                # place is harmless now that nothing reads the hide list.
                continue
            wanted.setdefault((mutation.ale_experiment_id, label), set()).add(mutation.id)

    for exp_filter in AleExperimentFilter.objects.all():
        note(_ids(exp_filter.ignored_mutations), "ignored mutations")
        note(_ids(exp_filter.starting_strain_mutations), "starting strain mutations")

    for global_filter in GlobalFilter.objects.all():
        note(_ids(global_filter.ignored_mutations), "global ignored mutations")

    return wanted


def forwards(apps, schema_editor):
    MutationChangeSet = apps.get_model("aledb_mutation_editor", "MutationChangeSet")
    MutationChange = apps.get_model("aledb_mutation_editor", "MutationChange")
    ObservedMutation = apps.get_model("aledb_seq", "ObservedMutation")
    DerivedDataState = apps.get_model("aledb_common", "DerivedDataState")

    from django.utils import timezone

    touched = False

    for (experiment_id, label), mutation_ids in sorted(_collect(apps).items()):
        observed = list(ObservedMutation.objects
                        .filter(mutation_id__in=mutation_ids)
                        .select_related("mutation"))
        if not observed:
            # The ids named nothing that is still observed anywhere. Nothing to record: the
            # list was already inert, which is what "nothing ever pruned these" produces.
            continue

        change_set = MutationChangeSet.objects.create(
            ale_experiment_id=experiment_id,
            created_by=None,
            kind="delete",
            note=_NOTE % label)
        # auto_now_add does not fire for a historical model, so stamp it here.
        MutationChangeSet.objects.filter(pk=change_set.pk).update(created_at=timezone.now())

        MutationChange.objects.bulk_create([
            MutationChange(
                change_set=change_set,
                operation="remove",
                sample_id=row.sequencing_experiment_id,
                mutation_id=row.mutation_id,
                observation=_observation_snapshot(row),
                mutation_identity=_mutation_identity(row.mutation))
            for row in observed])

        ObservedMutation.objects.filter(
            pk__in=[row.pk for row in observed]).delete()
        touched = True

    if touched:
        # Marked, not rebuilt: a migration is the wrong place to recompute an installation, and
        # every reader rebuilds its own on next view. The counts should come out identical --
        # these rows were already excluded -- but the stored numbers were computed under the
        # old filter, so they have to be recomputed to be trusted.
        DerivedDataState.objects.update(stale_since=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ("aledb_mutation_editor", "0001_initial"),
        ("aledb_filter", "0002_one_filter_per_experiment"),
        ("aledb_seq", "0009_backfill_reference_identity"),
        ("aledb_common", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

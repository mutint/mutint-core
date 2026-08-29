"""The mutation edit log: what changed, to which sample, by whom, and when.

Two tables, and the whole versioning scheme rests on them being **append-only**. A changeset
is never amended and never deleted; undoing one is a *new* changeset that happens to put
things back. That is what makes "the mutation set as of last Tuesday" a question with an
answer, and it is why restoring is not a rewind.

The unit of change is an `aledb_seq.ObservedMutation` -- one sample's observation of one
mutation -- and **never an `aledb_seq.Mutation`**. That distinction is load-bearing. Mutation
primary keys are stored as bare integers, with no foreign key and no pruning, in
aledb-phylogeny's `branch_mutations` JSON and in every exported CSV's "Mut ID" column.
Deleting a Mutation and letting a later re-import recreate it through `gd_import`'s
seven-field `get_or_create` would mint a new pk for the same biological mutation and quietly
invalidate all of that. Removing only the sample's observation of it changes nothing any
stored id means.

The other consequence of working at the observation level is that **no read path had to
change**. An ObservedMutation that is gone is gone; `mutation_table_builder`, `aledb_export`,
`aledb_stats`, `aledb_dashboard`, `aledb_search`, aledb-fixation and aledb-converge all keep
their unfiltered queries. A soft-delete flag would have needed every one of them taught to
filter, and the one that was missed would have gone on showing deleted mutations.
"""

from django.contrib.auth.models import User
from django.db import models

KIND_DELETE = "delete"
KIND_COPY = "copy"
KIND_ADD = "add"
KIND_RESTORE = "restore"
#: A mutation's own fields changed, everywhere it was observed. The *changeset* is labelled
#: this; its rows are still OP_REMOVE and OP_ADD, because that is what happened to every
#: observation -- each one moved from the old identity to the new. Nothing in `state_after`,
#: `plan_restore` or `restore` needed to learn about it.
KIND_EDIT = "edit"

KIND_CHOICES = [
    (KIND_DELETE, "Delete"),
    (KIND_COPY, "Copy"),
    (KIND_ADD, "Add"),
    (KIND_EDIT, "Edit"),
    (KIND_RESTORE, "Restore"),
]

OP_ADD = "add"
OP_REMOVE = "remove"

OPERATION_CHOICES = [
    (OP_ADD, "Added"),
    (OP_REMOVE, "Removed"),
]


class MutationChangeSet(models.Model):
    """One user action against one experiment. `created_at` is the version identifier.

    Scoped to an experiment rather than to a sample because that is the unit everything else
    uses: `?ale_experiment_id=` scopes every page, and the rebuild registry recomputes per
    experiment. One action may still touch many samples -- a batch copy does -- and a restore
    may be narrowed to a few of them.

    `created_by` is null for a changeset the system wrote. Migration 0002 is the only thing
    that does so today, and the history page renders those as "system" rather than as an
    unattributed user action.
    """

    ale_experiment = models.ForeignKey("aledb_experiment.AleExperiment",
                                       on_delete=models.CASCADE,
                                       related_name="mutation_changesets",
                                       db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    note = models.TextField(blank=True, default="")

    # Set only when kind == KIND_RESTORE: the changeset whose resulting state this restored
    # to. Null on a restore means "back to before any recorded change" -- i.e. what the import
    # produced. `kind` is what disambiguates that null, so no separate flag is needed.
    restored_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="restorations")

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return "%s on experiment %s at %s" % (self.kind, self.ale_experiment_id,
                                              self.created_at)

    @property
    def is_system(self):
        return self.created_by_id is None


class MutationChange(models.Model):
    """One ObservedMutation this changeset added or removed.

    `observation` is every column of the row, so a removal can be undone exactly rather than
    approximately -- frequency, the per-caller present flags and the read counts all come back
    as they were.

    `mutation` is nullable and `mutation_identity` exists because the Mutation row is not
    guaranteed to outlive the log. `aledb_import.ale_experiment._delete_all_orphaned_mutations`
    hard-deletes any Mutation with no ObservedMutation, and it runs after an experiment delete
    and after `delete_isolate` -- so removing a mutation's last observation makes it eligible
    for a sweep triggered by something else entirely. `mutation_identity` carries the exact
    seven-field `get_or_create` tuple `gd_import` dedups on, plus `gd_data` and `annotation`,
    which is enough to put the row back indistinguishable from an imported one. It is also
    exactly what the copy path needs, so one helper serves both.
    """

    change_set = models.ForeignKey(MutationChangeSet, on_delete=models.CASCADE,
                                   related_name="changes")
    operation = models.CharField(max_length=10, choices=OPERATION_CHOICES)
    sample = models.ForeignKey("aledb_seq.ResequencingExperiment", on_delete=models.CASCADE,
                               related_name="+")
    mutation = models.ForeignKey("aledb_seq.Mutation", on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="+")
    # Where a copy came from. Null for a delete, and for a restore that re-adds a row.
    source_sample = models.ForeignKey("aledb_seq.ResequencingExperiment",
                                      on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="+")

    observation = models.JSONField(default=dict)
    mutation_identity = models.JSONField(default=dict)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return "%s mutation %s on sample %s" % (self.operation, self.mutation_id,
                                                self.sample_id)

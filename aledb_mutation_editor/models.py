"""The mutation edit log: what changed, to which sample, by whom, and when.

Two tables, and the whole versioning scheme rests on them being **append-only**. An edit set
is never amended and never deleted; undoing one is a *new* edit set that happens to put
things back. That is what makes "the mutation set as of last Tuesday" a question with an
answer, and it is why restoring is not a rewind.

The unit of change is an `aledb_sample.MutationCall` -- one sample's call of one
mutation -- and **never an `aledb_sample.Mutation`**. That distinction is load-bearing. Mutation
primary keys are stored as bare integers, with no foreign key and no pruning, in
aledb-phylogeny's `branch_mutations` JSON and in every exported CSV's "Mut ID" column.
Deleting a Mutation and letting a later re-import recreate it through `gd_import`'s
seven-field `get_or_create` would mint a new pk for the same biological mutation and quietly
invalidate all of that. Removing only the sample's call of it changes nothing any
stored id means.

The other consequence of working at the call level is that **no read path had to
change**. A MutationCall that is gone is gone; `mutation_table_builder`, `aledb_export`,
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
#: A mutation's own fields changed, everywhere it was observed. The *edit set* is labeled
#: this; its rows are still OP_REMOVE and OP_ADD, because that is what happened to every
#: call -- each one moved from the old identity to the new. Nothing in `state_after`,
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


class MutationEditSet(models.Model):
    """One user action against one experiment. `created_at` is the version identifier.

    Scoped to an experiment rather than to a sample because that is the unit everything else
    uses: `?experiment_id=` scopes every page, and the rebuild registry recomputes per
    experiment. One action may still touch many samples -- a batch copy does -- and a restore
    may be narrowed to a few of them.

    `created_by` is null for an edit set the system wrote. Migration 0002 is the only thing
    that does so today, and the history page renders those as "system" rather than as an
    unattributed user action.
    """

    experiment = models.ForeignKey("aledb_experiment.Experiment",
                                       on_delete=models.CASCADE,
                                       related_name="mutation_edit_sets",
                                       db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    note = models.TextField(blank=True, default="")

    # Set only when kind == KIND_RESTORE: the edit set whose resulting state this restored
    # to. Null on a restore means "back to before any recorded change" -- i.e. what the import
    # produced. `kind` is what disambiguates that null, so no separate flag is needed.
    restored_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="restorations")

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return "%s on experiment %s at %s" % (self.kind, self.experiment_id,
                                              self.created_at)

    @property
    def is_system(self):
        return self.created_by_id is None


class MutationEdit(models.Model):
    """One MutationCall this edit set added or removed.

    `snapshot` is every column of the row, so a removal can be undone exactly rather than
    approximately -- `present`, `frequency`, `source` and the caller's `evidence` all come
    back as they were. That last one is a JSONField inside this JSONField, and is the reason
    `history.CALL_FIELDS` is worth reading before adding a column: a snapshot is built by
    walking that tuple, so a field missing from it is a field a restore silently drops.

    `mutation` is nullable and `mutation_identity` exists because the Mutation row is not
    guaranteed to outlive the log. `aledb_import.ale_experiment._delete_all_orphaned_mutations`
    hard-deletes any Mutation with no MutationCall, and it runs after an experiment delete
    and after `delete_sample` -- so removing a mutation's last call makes it eligible
    for a sweep triggered by something else entirely. `mutation_identity` carries the exact
    seven-field `get_or_create` tuple `gd_import` dedups on, plus `supplemental_data` and
    `annotation`,
    which is enough to put the row back indistinguishable from an imported one. It is also
    exactly what the copy path needs, so one helper serves both.
    """

    edit_set = models.ForeignKey(MutationEditSet, on_delete=models.CASCADE,
                                 related_name="edits")
    operation = models.CharField(max_length=10, choices=OPERATION_CHOICES)
    sample = models.ForeignKey("aledb_sample.Sample", on_delete=models.CASCADE,
                               related_name="+")
    mutation = models.ForeignKey("aledb_sample.Mutation", on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="+")
    # Where a copy came from. Null for a delete, and for a restore that re-adds a row.
    source_sample = models.ForeignKey("aledb_sample.Sample",
                                      on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="+")

    snapshot = models.JSONField(default=dict)
    mutation_identity = models.JSONField(default=dict)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return "%s mutation %s on sample %s" % (self.operation, self.mutation_id,
                                                self.sample_id)

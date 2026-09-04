from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.contrib.auth.models import User
from django.urls import reverse

from aledb_experiment import paths
from aledb_experiment.roles import ROLE_CHOICES, ROLE_OWNER

blank_field = {"blank": True, "null": True}


class SoftDeleteMixin(models.Model):
    """Deletion marks a row rather than destroying it.

    Only the top objects -- Project and Experiment -- carry the flag. Children are reached
    by traversing the FK chain when the purge command finally removes them, so a deletion is
    one row written rather than a cascade of them.

    Nothing filters these out automatically: `objects` stays unfiltered so the import paths'
    get_or_create and the CLI keep seeing every row. The user-facing list views exclude deleted
    rows explicitly via `live()`.
    """

    deleted_at = models.DateTimeField(db_index=True, **blank_field)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                   **blank_field)

    class Meta:
        abstract = True

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def soft_delete(self, user=None, when=None):
        from django.utils import timezone
        self.deleted_at = when or timezone.now()
        self.deleted_by = user if (user and user.is_authenticated) else None
        self.save(update_fields=["deleted_at", "deleted_by"])
        return self

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by"])
        return self


def live(queryset):
    """Exclude soft-deleted rows from a user-facing queryset."""
    return queryset.filter(deleted_at__isnull=True)


class Project(SoftDeleteMixin):
    name = models.CharField(max_length=50)
    # PROTECT, not DO_NOTHING: this column is NOT NULL and carries a real FK, so deleting a
    # project's primary owner was already impossible -- but as DO_NOTHING Django emitted no
    # handling for it and the refusal arrived from the database as an opaque IntegrityError at
    # commit, with the admin's confirmation page giving no warning and naming no project.
    # PROTECT asks the question up front and answers it with the list of projects in the way.
    # Ownership is transferred, not deleted out from under a project.
    user = models.ForeignKey(User, default=None, on_delete=models.PROTECT, help_text="project owner")
    date = models.DateTimeField(auto_now_add=True, help_text="project created date")
    is_public = models.BooleanField(default=False)
    PROJECT_STATUS = (('new', 'New'), ('in progress', 'In progress'), ('completed', 'Completed'))
    status = models.CharField(max_length=25, default='new', blank=True, choices=PROJECT_STATUS)
    description = models.CharField(max_length=300)

    def owner(self):
        return self.user.get_full_name()

    def experiments(self):
        return live(Experiment.objects.filter(project=self))

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("project_detail", args=(self.pk,))

    def date_str(self):
        if self.date:
            return self.date.strftime("%Y-%m-%d")
        return ''


class Experiment(SoftDeleteMixin):
    # The primary key is Django's implicit `id`, and used to be an explicit
    # `ale_id = AutoField(primary_key=True)`. Two things were wrong with that.
    #
    # It made `ale_id` mean three different things depending on what you were holding: this
    # experiment's pk, the population's text label, and the time point's foreign key to a
    # population row. Every query in the suite traverses that chain, so "the path ending in
    # ale_id" was two destinations and a reader could not tell which.
    #
    # And being explicit meant DEFAULT_AUTO_FIELD did not reach it: this was the only
    # 32-bit `integer` primary key left among 26 `bigint` ones, and every foreign key
    # pointing at it was 32-bit too.
    #
    # The query parameter has always been `experiment_id`, so the URLs did not move.
    name = models.CharField(max_length=200)
    person = models.CharField(max_length=200)
    date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(**blank_field)
    project = models.ForeignKey(Project, default=None, **blank_field, on_delete=models.DO_NOTHING)
    doi = models.TextField(**blank_field)

    # --- the lock ---------------------------------------------------------------------
    #
    # Shaped like SoftDeleteMixin above: the timestamp *is* the flag, and there is no
    # boolean beside it to disagree with. A locked experiment refuses every write in the
    # web UI regardless of who is asking -- an admin unlocks, edits, and locks it again.
    # That is what makes it a lock rather than a fifth role: it guards against the
    # accidental edit to a finished dataset, which no permission tier can.
    #
    # There is no `locked_reason`. It was a third column and a text box on the lock dialog,
    # and the dialog is a plain confirm now: the question a lock has to answer is whether
    # this dataset is closed, and who to ask about it, both of which the two columns below
    # carry. A free-text field nobody is required to fill in cannot be relied on for either.
    locked_at = models.DateTimeField(db_index=True, **blank_field)
    locked_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                  **blank_field)

    # --- the designated ancestor ------------------------------------------------------
    #
    # The sample this experiment started from. Its mutations are the starting line rather
    # than evolution, so they are subtracted from every other sample before anything is
    # computed, and the sample itself leaves every listing and every analysis.
    # `aledb_experiment/ancestor.py` is the whole mechanism.
    #
    # The FK *is* the flag, as `locked_at` is: null means no ancestor, with no boolean
    # beside it to disagree. One column on the experiment also makes "exactly one per
    # experiment" structural -- designating a new one is a single UPDATE, so there is no
    # prior flag left to clear and no way to end up with two.
    #
    # Named by string because `aledb_experiment` must not import `aledb_sample` at load time;
    # the dependency runs the other way, which is why `Sample.time_point` names its own
    # target the same way.
    #
    # SET_NULL: deleting the sample leaves the experiment simply without an ancestor.
    # That the sample belongs to *this* experiment cannot be a database constraint, so
    # `aledb_experiment.views.experiment_ancestor_apply` checks it.
    #
    # The two columns beside it are not decoration. This is shared state that changes what
    # everyone sees, which is exactly what `AleExperimentFilter` got wrong -- one row per
    # experiment that anybody with write access could change silently, with no record of
    # who did it. Attribution is the difference between that and this.
    ancestor = models.ForeignKey("aledb_sample.Sample",
                                 on_delete=models.SET_NULL, related_name="ancestor_of",
                                 **blank_field)
    ancestor_set_at = models.DateTimeField(**blank_field)
    ancestor_set_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                        **blank_field)

    class Meta:
        verbose_name_plural = "experiments"

    def __unicode__(self):
        return "#%d-%s" % (self.id, self.name)

    def __str__(self):
        return self.name

    @property
    def is_locked(self):
        return self.locked_at is not None

    def lock(self, user=None, when=None):
        from django.utils import timezone
        self.locked_at = when or timezone.now()
        self.locked_by = user if (user and user.is_authenticated) else None
        self.save(update_fields=["locked_at", "locked_by"])
        return self

    def unlock(self):
        """Clear the lock. Takes no user, as `SoftDeleteMixin.restore()` does not.

        So the columns record who last *locked* it, not who unlocked it. Who unlocked it
        is a question for an audit log rather than two columns, and there is no such log
        for experiments -- `aledb_mutation_editor`'s edit sets cover the mutations only.
        """
        self.locked_at = None
        self.locked_by = None
        self.save(update_fields=["locked_at", "locked_by"])
        return self

    @property
    def has_ancestor(self):
        return self.ancestor_id is not None

    def set_ancestor(self, reseq, user=None):
        """Designate `reseq` as this experiment's ancestor, replacing any prior one."""
        from django.utils import timezone
        self.ancestor = reseq
        self.ancestor_set_at = timezone.now()
        self.ancestor_set_by = user if (user and user.is_authenticated) else None
        self.save(update_fields=["ancestor", "ancestor_set_at", "ancestor_set_by"])
        return self

    def clear_ancestor(self):
        """Forget the designation. All three columns go, as `unlock()` clears both of its."""
        self.ancestor = None
        self.ancestor_set_at = None
        self.ancestor_set_by = None
        self.save(update_fields=["ancestor", "ancestor_set_at", "ancestor_set_by"])
        return self

    def lock_message(self):
        """Why a write was refused, written for the person who tried it."""
        if not self.is_locked:
            return ""
        return ("%s is locked, so it cannot be changed. An administrator of its project "
                "can unlock it." % self.name)

    def doi_as_list(self):
        if self.doi is None:
            return []
        return self.doi.split(' ')

    def get_absolute_url(self):
        return reverse("experiment_detail", args=(self.pk,))

    def date_str(self):
        if self.date:
            return self.date.strftime("%Y-%m-%d")
        return ''

    def experiment_context(self) -> dict:
        """The four things base.html needs to render the experiment in the sidebar.

        It returns the experiment's own name and the project's separately, because that is
        how the template joins them: `{{ ale_project_name }}: {{ experiment_name }}`.
        This used to return a *composed* `"project: experiment"` under the experiment key
        and no project key at all, so a caller that trusted it rendered `": project:
        experiment"` -- a stray leading colon -- and one that added the project name without
        also overriding the composed one rendered the project twice.

        Every experiment-scoped view was carrying its own workaround for that, and the two
        that were not carried the bug: the sample edit pages had the leading colon, the
        genome browser and the Add Data page had the doubled name.

        `project` is nullable, so the project name can be empty; the template renders the
        colon regardless, which is a template question rather than this one's.
        """
        return {
            "experiment_name": self.name,
            "experiment_id": self.id,
            "ale_project_name": self.project.name if self.project else "",
            "ale_project_id": self.project_id,
            # Fifth, and here for the same reason as the other four: every experiment-scoped
            # page renders the shell, and a locked experiment should say so on all of them
            # rather than only on the one page that happens to check.
            "ale_experiment_locked": self.is_locked,
            # Sixth, and for the same reason: a page that hides the ancestor should be able
            # to say so without asking the database again.
            "ale_experiment_ancestor_id": self.ancestor_id,
        }


class Population(models.Model):
    """One evolving lineage within an experiment.

    **This was `AleId`, and its own docstring already called these populations.** The model
    carried `# TODO: this model should be called "Ale"` while describing `Ara-1` and `Ara+1`
    as "two different LTEE populations" -- the biology had the word all along and the schema
    did not.

    `name` is **text**, not a number (`0008`), and so is the sample's. Real lineage names are
    labels: `Ara-1` and `Ara+1` both end in 1, so any rule reducing them to an integer merges
    them. `TimePoint.value` is the one member of the chain that stays an `IntegerField`,
    because a time point is a genuine ordinal -- aledb-fixation sorts by it and takes a
    population's last two.

    Ordering is therefore lexicographic unless asked otherwise, which puts `10` before `2`.
    `aledb_experiment.ordering.sample_order()` is what every sample listing orders by
    instead; see its docstring.
    """
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, **blank_field)
    species = models.CharField(max_length=300, **blank_field)
    strain = models.CharField(max_length=300, **blank_field)
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE)

    def __unicode__(self):
        return "Population %s < %s" % (self.name, self.experiment)

    class Meta:
        unique_together = (("experiment", "name"),)

        verbose_name_plural = "populations"


class Media(models.Model):
    temperature = models.CharField(max_length=200,default='37',
                                    help_text="Temperature in Celcius")
    description = models.CharField(max_length=200)
    carbon_source = models.CharField(max_length=200,
                                 default=None,
                                 **blank_field)
    nitrogen_source = models.CharField(max_length=200,
                                 default=None,
                                 **blank_field)
    phosphorus_source = models.CharField(max_length=200,
                                 default=None,
                                 **blank_field)
    sulfur_source = models.CharField(max_length=200,
                                 default=None,
                                 **blank_field)
    calcium_source = models.CharField(max_length=200,
                                     default=None,
                                     **blank_field)
    supplement = models.CharField(max_length=200,
                                     default=None,
                                     **blank_field)

    # TODO: figure out components
    # maybe carbon source, etc.? or track individual chemicals
    def experiments(self):
        """The experiments with a time point grown in this medium.

        It read `TimePoint.objects.filter(project=self)` and that model had no `project`, so this
        raised FieldError every time it ran -- and `MediaAdmin.list_display` calls it, which
        makes /admin/aledb_experiment/media/ a 500 rather than a page.
        """
        return live(Experiment.objects.filter(
            **{paths.down_chain("experiment", upto="timepoint") + "__media": self}
        )).distinct()

    experiments.short_description = 'Experiment'

    class Meta:
        verbose_name_plural = "Media"


class TimePoint(models.Model):
    """When along a population's history a sample was taken.

    **This was `TimePoint`**, and every label a person could see already said "Time point" --
    the form field, its validation message, and a test class named for it. A flask is the
    vessel one kind of experiment happens to use; what the column means is the point in
    time.

    `value` is the one member of the chain that is genuinely a number: aledb-fixation orders
    by it and takes a population's last two.
    """
    population = models.ForeignKey(Population, on_delete=models.CASCADE)
    value = models.IntegerField(**blank_field)
    media = models.ForeignKey(Media, on_delete=models.DO_NOTHING)

    def __unicode__(self):
        return "Time point %s < %s" % (self.value, self.population)

    def experiment(self):
        return self.population.experiment.id

    class Meta:
        unique_together = (("population", "value"),)

        verbose_name_plural = "time points"


# The TODO that stood here asked for `reseq_reference` to be called `reseq_ref_name`. It
# is `Sample.reference_genome` now, which says the same thing without the abbreviation.
#TODO: Change 'library_prep' field to 'wgs_kit'
# `Isolate` and `TechnicalReplicate` stood here. Both are gone, folded into
# `aledb_sample.Sample` -- the sample row itself -- along with everything they held. See that
# model's docstring for why the merge went that way round rather than the other. The chain
# is `Experiment -> Population -> TimePoint -> Sample` now.


# --- sharing: groups and project access ---------------------------------------------------
#
# Access is granted at the project level and nowhere else. An experiment, a sample and a
# mutation are all reached through `experiment.project`, so there is exactly one place to ask
# the question and exactly one place to change the answer.
#
# The policy that reads these tables is `aledb_experiment/permissions.py`; the ordering
# between roles is `aledb_experiment/roles.py`. Nothing here decides who may do what.


class UserGroup(models.Model):
    """A named set of people, so a whole lab can be given access in one grant.

    It was `AleGroup`, which was the only thing in the schema called ALE that had nothing to
    do with one -- a group is people, and the same group is used across projects that are
    not ALE experiments at all.

    Deliberately not `django.contrib.auth.Group`: that one is administered from /admin/, has
    no notion of who owns it, and carries a `permissions` m2m that would sit unused and
    invite someone to wire Django model permissions into a per-object scheme.

    `owner` is PROTECT, not the DO_NOTHING that `Project.user` uses for historical reasons: a
    group whose owner has been deleted is unmanageable -- nobody can rename it, add to it, or
    delete it -- and ownership transfer exists precisely so a departing member's groups get
    handed on first.
    """

    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True, default="")
    owner = models.ForeignKey(User, on_delete=models.PROTECT,
                              related_name="owned_user_groups")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Case-insensitive, because a group is added to a project by typing its name into a
        # plain text box. Two groups differing only in case would be unresolvable, and the
        # person typing would have no way to say which they meant.
        constraints = [
            models.UniqueConstraint(Lower("name"), name="usergroup_name_ci_unique"),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("group_detail", args=(self.pk,))

    def member_count(self):
        return self.memberships.count()


class UserGroupMembership(models.Model):
    """One person's place in one group.

    `is_manager` is a flag rather than a separate `managers` m2m so that "every manager is a
    member" is true by construction. With two tables it is only true by convention, and every
    membership query then has to union them.

    The group's owner holds a row here too, with `is_manager=True`, written when the group is
    created. That keeps `group.memberships` a complete roster, which is what lets the
    permission query reach a group's owner through the same join as everyone else instead of
    needing a `Q(group__owner=user)` special case in the hot path.
    """

    group = models.ForeignKey(UserGroup, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name="user_group_memberships")
    is_manager = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                 **blank_field)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "user"],
                                    name="usergroup_one_row_per_member"),
        ]
        ordering = ["-is_manager", "user__username"]

    def __str__(self):
        return "%s in %s" % (self.user.get_username(), self.group.name)


class ProjectAccess(models.Model):
    """One grant: a role on a project, held by either a user or a group.

    The unique constraints have to be **conditional**. A plain
    `unique_together = ("project", "user", "group")` looks equivalent and is not: SQL treats
    NULLs as distinct, so it would permit unlimited duplicate rows for the same group (whose
    `user` is NULL) and the same user (whose `group` is NULL).

    **A group may not hold `owner`.** Ownership is accountability, and "who is the last owner"
    has to be answerable about a person. It also closes the obvious escalation: were it
    allowed, a group's manager could add themselves to the group and become an owner of every
    project that group owns.
    """

    project = models.ForeignKey('Project', on_delete=models.CASCADE,
                                related_name="access_entries")
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name="project_access", **blank_field)
    group = models.ForeignKey(UserGroup, on_delete=models.CASCADE,
                              related_name="project_access", **blank_field)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                   **blank_field)

    class Meta:
        verbose_name_plural = "project access"
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                       | Q(user__isnull=True, group__isnull=False)),
                name="projectaccess_exactly_one_subject"),
            models.UniqueConstraint(fields=["project", "user"],
                                    condition=Q(user__isnull=False),
                                    name="projectaccess_one_row_per_user"),
            models.UniqueConstraint(fields=["project", "group"],
                                    condition=Q(group__isnull=False),
                                    name="projectaccess_one_row_per_group"),
            models.CheckConstraint(
                condition=~Q(group__isnull=False, role=ROLE_OWNER),
                name="projectaccess_groups_cannot_own"),
        ]

    def subject_kind(self):
        return "user" if self.user_id else "group"

    def subject_name(self):
        return self.user.get_username() if self.user_id else self.group.name

    def __str__(self):
        return "%s: %s on %s" % (self.subject_name(), self.role, self.project.name)

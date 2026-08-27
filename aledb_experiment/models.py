from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.contrib.auth.models import User
from django.urls import reverse

from aledb_experiment.roles import ROLE_CHOICES, ROLE_OWNER

blank_field = {"blank": True, "null": True}


class SoftDeleteMixin(models.Model):
    """Deletion marks a row rather than destroying it.

    Only the top objects -- Project and AleExperiment -- carry the flag. Children are reached
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


class Instrument(models.Model):

    name = models.CharField(max_length=200)

    def __unicode__(self):
        return self.name

    def __str__(self):
        return self.name


class Project(SoftDeleteMixin):
    name = models.CharField(max_length=50)
    user = models.ForeignKey(User, default=None, on_delete=models.DO_NOTHING, help_text="project owner")
    date = models.DateTimeField(auto_now_add=True, help_text="project created date")
    is_public = models.BooleanField(default=False)
    PROJECT_STATUS = (('new', 'New'), ('in progress', 'In progress'), ('completed', 'Completed'))
    status = models.CharField(max_length=25, default='new', blank=True, choices=PROJECT_STATUS)
    description = models.CharField(max_length=300)

    def owner(self):
        return self.user.get_full_name()

    def experiments(self):
        return live(AleExperiment.objects.filter(project=self))

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("project_detail", args=(self.pk,))

    def date_str(self):
        if self.date:
            return self.date.strftime("%Y-%m-%d")
        return ''


class AleExperiment(SoftDeleteMixin):
    ale_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200)
    person = models.CharField(max_length=200)
    date = models.DateTimeField(auto_now_add=True)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
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
    # `locked_reason` is the one thing SoftDeleteMixin has no equivalent of, and it earns
    # its place: every refusal in the app can then say why rather than only no.
    locked_at = models.DateTimeField(db_index=True, **blank_field)
    locked_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                  **blank_field)
    locked_reason = models.TextField(blank=True, default="")

    class Meta:
        verbose_name_plural = "experiments"

    def __unicode__(self):
        return "#%d-%s" % (self.ale_id, self.name)

    def __str__(self):
        return self.name

    @property
    def is_locked(self):
        return self.locked_at is not None

    def lock(self, user=None, reason="", when=None):
        from django.utils import timezone
        self.locked_at = when or timezone.now()
        self.locked_by = user if (user and user.is_authenticated) else None
        self.locked_reason = (reason or "").strip()
        self.save(update_fields=["locked_at", "locked_by", "locked_reason"])
        return self

    def unlock(self):
        """Clear the lock. Takes no user, as `SoftDeleteMixin.restore()` does not.

        So the columns record who last *locked* it, not who unlocked it. Who unlocked it
        is a question for an audit log rather than two columns, and there is no such log
        for experiments -- `aledb_mutation_editor`'s changesets cover the mutations only.
        """
        self.locked_at = None
        self.locked_by = None
        self.locked_reason = ""
        self.save(update_fields=["locked_at", "locked_by", "locked_reason"])
        return self

    def lock_message(self):
        """Why a write was refused, written for the person who tried it."""
        if not self.is_locked:
            return ""
        message = "%s is locked, so it cannot be changed." % self.name
        if self.locked_reason:
            message += " Reason: %s" % self.locked_reason
        return message + " An administrator of its project can unlock it."

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
        how the template joins them: `{{ ale_project_name }}: {{ ale_experiment_name }}`.
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
            "ale_experiment_name": self.name,
            "ale_experiment_id": self.ale_id,
            "ale_project_name": self.project.name if self.project else "",
            "ale_project_id": self.project_id,
            # Fifth, and here for the same reason as the other four: every experiment-scoped
            # page renders the shell, and a locked experiment should say so on all of them
            # rather than only on the one page that happens to check.
            "ale_experiment_locked": self.is_locked,
        }


# TODO: this model should be called "Ale".
class AleId(models.Model):
    """Parallel ALE's run within an ALE experiment"""
    ale_id = models.IntegerField()
    description = models.CharField(max_length=300, **blank_field)
    species = models.CharField(max_length=300, **blank_field)
    strain = models.CharField(max_length=300, **blank_field)
    ale_experiment = models.ForeignKey(AleExperiment, on_delete=models.CASCADE)
    starting_strain = models.ForeignKey("Isolate", on_delete=models.DO_NOTHING,
                                        default=None,
                                        **blank_field)

    def __unicode__(self):
        # return "ALE #%d < %s" % (self.ale_id, self.ale_experiment.name)
        return "ALE #%d < %s" % (self.ale_id, self.ale_experiment)

    class Meta:
        unique_together = (("ale_experiment", "ale_id"),)

        verbose_name_plural = "ALEs"


class Media(models.Model):
    temperature = models.CharField(max_length=200,default='37',
                                    help_text="Temperature in Celcius")
    volume = models.FloatField(default=25,
                               help_text="Volume of culture in each flask (mL)")
    stirring_speed = models.FloatField(default=1123,
                                       help_text="RPM")
    description = models.CharField(max_length=200)
    substrate = models.CharField(max_length=200,
                                 default=None,
                                 **blank_field)
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
    other = models.TextField(**blank_field)

    # TODO: figure out components
    # maybe carbon source, etc.? or track individual chemicals
    def __unicode__(self):
        return "%s (%.1f C, %.1f mL, %.1f RPM)" % \
               (self.description,
                self.temperature,
                self.volume,
                self.stirring_speed)

    def experiments(self):
        return Flask.objects.filter(project=self).values("ale_id").values("ale_experiment").distinct()

    experiments.short_description = 'Experiment'

    class Meta:
        verbose_name_plural = "Media"


class FreezerBox(models.Model):

    name = models.CharField(max_length=500,
                            help_text="A unique name that identifies the box from other boxes")

    number = models.IntegerField(default=1,
                                 help_text="Start with 1. If another box with the same name is needed label it with 2, 3 etc... Make sure this box number appears on the label")

    location = models.CharField(max_length=500,
                                null=True,
                                default="None",
                                help_text="Where is the box located")

    location_last_updated = models.DateField(auto_now=True,
                                             null=True,
                                             help_text="Date when location was last updated")

    def __unicode__(self):
        return "Box #%i - %s" % (self.number,
                                 self.name)

    class Meta:
        verbose_name_plural = "Freezer Boxes"


class Flask(models.Model):
    ale_id = models.ForeignKey(AleId, on_delete=models.CASCADE)
    flask_number = models.IntegerField(**blank_field)
    media = models.ForeignKey(Media, on_delete=models.DO_NOTHING)
    comments = models.CharField(max_length=200, **blank_field)

    def __unicode__(self):
        if self.ale_id.description is not None:
            if self.ale_id.description.lower() == ('Not from ALE').lower():
                return 'Not from ALE'
            else:
                return "Flask#%d < %s" % (self.flask_number,
                                          self.ale_id)
        else:
            return "Flask#%d < %s" % (self.flask_number,
                                      self.ale_id)

    def ale_experiment(self):
        return self.ale_id.ale_experiment.ale_id
    class Meta:
        unique_together = (("ale_id",
                            "flask_number"),)

        verbose_name_plural = "Flasks"


#TODO: Change 'reseq_reference' field to 'reseq_ref_name'
#TODO: Change 'library_prep' field to 'wgs_kit'
class Isolate(models.Model):
    isolate_number = models.IntegerField()
    parent_isolate = models.ForeignKey("Isolate", on_delete=models.DO_NOTHING, **blank_field)
    flask = models.ForeignKey(Flask, on_delete=models.CASCADE)
    is_population = models.BooleanField()
    freezer_box = models.ForeignKey(FreezerBox, on_delete=models.DO_NOTHING)
    description = models.CharField(max_length=300, **blank_field)
    person = models.CharField(max_length=200, **blank_field)
    reseq_reference = models.CharField(max_length=200, **blank_field)
    reseq_date = models.CharField(max_length=200, **blank_field)
    breseq_version = models.CharField(max_length=200, **blank_field)
    library_prep = models.CharField(max_length=200, **blank_field)


    def __unicode__(self):
        if self.flask.ale_id.description is not None:
            if self.flask.ale_id.description.lower() == ('Not from ALE').lower():
                return self.description
            else:
                population_or_clonal = "POP" if self.is_population else "COL"
                parent = self.parent_isolate if self.parent_isolate else self.flask
                return "#%d %s < %s" % (self.isolate_number,
                                        population_or_clonal,
                                        parent)
        else:
            population_or_clonal = "POP" if self.is_population else "COL"
            parent = self.parent_isolate if self.parent_isolate else self.flask
            return "#%d %s < %s" % (self.isolate_number,
                                    population_or_clonal,
                                    parent)


class TechnicalReplicate(models.Model):
    tech_rep_number = models.IntegerField(default=1)
    isolate = models.ForeignKey(Isolate, on_delete=models.CASCADE)
    tags = models.CharField(max_length=500, **blank_field)
    description = models.CharField(max_length=500, **blank_field)


# TODO: what are these integers referring to. If ALE Experiment, model should be moved to ale.models and use foreign keys.
class RecentExperiments(models.Model):
    first = models.IntegerField(null=True)
    second = models.IntegerField(null=True)
    third = models.IntegerField(null=True)
    fourth = models.IntegerField(null=True)
    fifth = models.IntegerField(null=True)


# --- sharing: groups and project access ---------------------------------------------------
#
# Access is granted at the project level and nowhere else. An experiment, a sample and a
# mutation are all reached through `experiment.project`, so there is exactly one place to ask
# the question and exactly one place to change the answer.
#
# The policy that reads these tables is `aledb_experiment/permissions.py`; the ordering
# between roles is `aledb_experiment/roles.py`. Nothing here decides who may do what.


class AleGroup(models.Model):
    """A named set of people, so a whole lab can be given access in one grant.

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
                              related_name="owned_ale_groups")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Case-insensitive, because a group is added to a project by typing its name into a
        # plain text box. Two groups differing only in case would be unresolvable, and the
        # person typing would have no way to say which they meant.
        constraints = [
            models.UniqueConstraint(Lower("name"), name="alegroup_name_ci_unique"),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("group_detail", args=(self.pk,))

    def member_count(self):
        return self.memberships.count()


class AleGroupMembership(models.Model):
    """One person's place in one group.

    `is_manager` is a flag rather than a separate `managers` m2m so that "every manager is a
    member" is true by construction. With two tables it is only true by convention, and every
    membership query then has to union them.

    The group's owner holds a row here too, with `is_manager=True`, written when the group is
    created. That keeps `group.memberships` a complete roster, which is what lets the
    permission query reach a group's owner through the same join as everyone else instead of
    needing a `Q(group__owner=user)` special case in the hot path.
    """

    group = models.ForeignKey(AleGroup, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name="ale_group_memberships")
    is_manager = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                 **blank_field)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "user"],
                                    name="alegroup_one_row_per_member"),
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
    group = models.ForeignKey(AleGroup, on_delete=models.CASCADE,
                              related_name="project_access", **blank_field)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="+",
                                   **blank_field)

    class Meta:
        verbose_name_plural = "project access"
        constraints = [
            models.CheckConstraint(
                check=(Q(user__isnull=False, group__isnull=True)
                       | Q(user__isnull=True, group__isnull=False)),
                name="projectaccess_exactly_one_subject"),
            models.UniqueConstraint(fields=["project", "user"],
                                    condition=Q(user__isnull=False),
                                    name="projectaccess_one_row_per_user"),
            models.UniqueConstraint(fields=["project", "group"],
                                    condition=Q(group__isnull=False),
                                    name="projectaccess_one_row_per_group"),
            models.CheckConstraint(
                check=~Q(group__isnull=False, role=ROLE_OWNER),
                name="projectaccess_groups_cannot_own"),
        ]

    def subject_kind(self):
        return "user" if self.user_id else "group"

    def subject_name(self):
        return self.user.get_username() if self.user_id else self.group.name

    def __str__(self):
        return "%s: %s on %s" % (self.subject_name(), self.role, self.project.name)

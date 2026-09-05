"""Inspect and change project access from the shell.

Exists chiefly as the escape hatch for one deliberate behavior change: `can_view_project`
used to end `return bool(user.is_staff)`, and `load_projects` creates every imported user
with `is_staff=True`, so nearly everyone could read nearly everything. That clause is gone.
The migration converts every grant that was properly issued, but a deployment that leaned on
the staff clause will find people missing projects, and this is how an operator puts a
specific person back without reaching into the database.

    ./mutint project_access list [--project 4]
    ./mutint project_access grant --project 4 --user alice --role read
    ./mutint project_access grant --project 4 --group "Barrick Lab" --role write
    ./mutint project_access revoke --project 4 --user alice
"""

from django.core.management.base import BaseCommand, CommandError

from mutint_experiment.models import UserGroup, Project, ProjectAccess
from mutint_experiment.permissions import (
    AccessError, grant_project_access, revoke_project_access,
)
from mutint_experiment.roles import ROLE_RANK


class Command(BaseCommand):
    help = "List, grant or revoke access to a project."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list", "grant", "revoke"])
        parser.add_argument("--project", type=int,
                            help="project id; omit with `list` to show every project")
        parser.add_argument("--user", help="username")
        parser.add_argument("--group", help="group name")
        parser.add_argument("--role", choices=sorted(ROLE_RANK, key=ROLE_RANK.get),
                            help="read, write, admin or owner")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "list":
            return self._list(options)

        project = self._project(options)
        subject = self._subject(options)
        if action == "grant":
            role = options.get("role")
            if not role:
                raise CommandError("grant needs --role.")
            try:
                entry = grant_project_access(project, subject, role)
            except AccessError as refusal:
                raise CommandError(str(refusal))
            self.stdout.write("%s now has %s on %s." % (entry.subject_name(), entry.role,
                                                        project.name))
            return

        entry = ProjectAccess.objects.filter(
            project=project,
            **({"user": subject} if not isinstance(subject, UserGroup) else {"group": subject})
        ).first()
        if entry is None:
            raise CommandError("No such grant on %s." % project.name)
        try:
            revoke_project_access(project, entry)
        except AccessError as refusal:
            raise CommandError(str(refusal))
        self.stdout.write("Revoked.")

    # --- helpers ---------------------------------------------------------------------

    def _project(self, options):
        if not options.get("project"):
            raise CommandError("Name a project with --project <id>.")
        project = Project.objects.filter(pk=options["project"]).first()
        if project is None:
            raise CommandError("There is no project %s." % options["project"])
        return project

    def _subject(self, options):
        from django.contrib.auth.models import User

        username, group_name = options.get("user"), options.get("group")
        if bool(username) == bool(group_name):
            raise CommandError("Name either --user or --group, not both.")
        if username:
            # Not `resolve_username`: that one is deliberately forgiving about case for a
            # text box. At a shell an exact name is expected and a near miss should say so.
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError('There is no user named "%s".' % username)
            return user
        group = UserGroup.objects.filter(name__iexact=group_name).first()
        if group is None:
            raise CommandError('There is no group named "%s".' % group_name)
        return group

    def _list(self, options):
        projects = Project.objects.all().order_by("pk")
        if options.get("project"):
            projects = projects.filter(pk=options["project"])
            if not projects.exists():
                raise CommandError("There is no project %s." % options["project"])

        for project in projects:
            marker = " (deleted)" if project.is_deleted else ""
            public = " [public]" if project.is_public else ""
            self.stdout.write("%s: %s%s%s" % (project.pk, project.name, public, marker))
            entries = (ProjectAccess.objects.filter(project=project)
                       .select_related("user", "group").order_by("user__username",
                                                                 "group__name"))
            if not entries:
                self.stdout.write("    (no grants)")
            for entry in entries:
                primary = " *primary" if entry.user_id == project.user_id else ""
                self.stdout.write("    %-8s %s %s%s" % (entry.role, entry.subject_kind(),
                                                        entry.subject_name(), primary))

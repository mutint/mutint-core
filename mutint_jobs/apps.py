from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = 'mutint_jobs'

    def ready(self):
        # No `register_nav_item`, and that is deliberate. `nav_registry` has no per-user
        # visibility concept, so a registered entry renders for anonymous visitors and leads
        # straight to a 403 -- the dead end this codebase refuses elsewhere (see the comment
        # in project/list.html). The link is written into base.html's account block instead,
        # beside Logout and Change Password, where `{% if user.is_authenticated %}` already
        # governs it. It belongs there on the merits too: this is a page about you.
        #
        # Adding `visible_to=` to nav_registry for one entry would be a mechanism with a
        # single producer, which is the reasoning mutint-core/CLAUDE.md already records for the
        # account block itself.
        pass

from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "mutint_common"

    def ready(self):
        # mutint-core's own About section is registered from here rather than from
        # mutint_about, because this app owns version.py. mutint_about owns the *page*; what
        # the page says about the platform is the platform's to say. Any one of mutint-core's
        # apps could register it -- the entry is keyed by the checkout they all share.
        from mutint_common.about_registry import register_about_section
        from mutint_common.version import __version__

        register_about_section(self, name='mutint-core', version=__version__,
                               template='about/sections/mutint_core.html')

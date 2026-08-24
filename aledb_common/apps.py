from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "aledb_common"

    def ready(self):
        # aledb-core's own About section is registered from here rather than from
        # aledb_about, because this app owns version.py. aledb_about owns the *page*; what
        # the page says about the platform is the platform's to say. Any one of aledb-core's
        # apps could register it -- the entry is keyed by the checkout they all share.
        from aledb_common.about_registry import register_about_section
        from aledb_common.version import __version__

        register_about_section(self, name='aledb-core', version=__version__,
                               template='about/sections/aledb_core.html')

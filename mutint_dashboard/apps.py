from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "mutint_dashboard"

    def ready(self):
        from mutint_common.rebuild_registry import (
            PRIORITY_AGGREGATE, SITE_SCOPE, register_rebuilder,
        )
        from mutint_common.storage_registry import UNATTRIBUTED_REBUILD
        from mutint_dashboard.util import (
            rebuild_mutation_counts, rebuild_sample_counts, rebuild_storage_unattributed,
        )

        # **A nav entry only when the brand does not lead here.** The dashboard is normally
        # what the sidebar's own brand links to -- an inventory of the whole installation is
        # what somebody clicking the site's name is asking for -- and a second entry three
        # rows below it said the same thing twice. But `MUTINT_BRANDING['url']` lets a
        # deployment point that brand somewhere else (MutInt sends it to the source
        # repository), and then the dashboard is reachable from nowhere at all.
        #
        # So the condition is exactly the thing that changed, asked once at startup: if the
        # brand still leads here, no entry; if it has been pointed away, put the entry back.
        # Self-correcting in both directions, and it keeps the original reasoning true rather
        # than deleting it. See `navbar-brand` in mutint_common/templates/base.html.
        from django.conf import settings

        from mutint_common.nav_registry import MAIN_SECTION, register_nav_item

        if getattr(settings, "MUTINT_BRANDING", {}).get("url"):
            register_nav_item('Dashboard', url='/dashboard', section=MAIN_SECTION)

        # Site-scoped and last: both count rows across every experiment, so they are only
        # right once each experiment's own derived data is. Registered separately rather than
        # as `rebuild_dashboard_data`, which is the two of them together, because they are
        # invalidated by different things -- a sample renumber changes the sample counts and
        # provably nothing about a mutation count, and `rebuild_after_structural_change` has
        # always refused to pay for the second. `only=` is how that refusal is now expressed.
        register_rebuilder('sample_counts', rebuild_sample_counts, scope=SITE_SCOPE,
                           label='Dashboard sample counts', priority=PRIORITY_AGGREGATE)
        # This declared `inputs={INPUT_MUTATIONS}` so that a filter edit -- which marked every
        # experiment's derived data at once -- would not mark the most expensive rebuild there
        # is. Nothing can edit a filter for anybody but themselves now, so there is no such
        # edit and no vocabulary left to say it with. The dashboard still applies no filter, for
        # the reason `_live_call_rows` gives.
        register_rebuilder('mutation_counts', rebuild_mutation_counts, scope=SITE_SCOPE,
                           label='Dashboard mutation counts', priority=PRIORITY_AGGREGATE)
        # Bytes in the store nothing owns. Named by the storage registry's constant rather
        # than spelled here, because `request_remeasure` marks it from core and from every
        # plugin that moves files, and two spellings would mean one of them marks nothing.
        register_rebuilder(UNATTRIBUTED_REBUILD, rebuild_storage_unattributed,
                           scope=SITE_SCOPE, label='Unattributed stored data',
                           priority=PRIORITY_AGGREGATE)

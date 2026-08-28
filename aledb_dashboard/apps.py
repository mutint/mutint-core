from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "aledb_dashboard"

    def ready(self):
        from aledb_common.nav_registry import (
            MAIN_SECTION, register_nav_item,
        )
        from aledb_common.rebuild_registry import (
            INPUT_MUTATIONS, PRIORITY_AGGREGATE, SITE_SCOPE, register_rebuilder,
        )
        from aledb_dashboard.util import rebuild_mutation_counts, rebuild_sample_counts

        register_nav_item('Dashboard', url='/dashboard', section=MAIN_SECTION)

        # Site-scoped and last: both count rows across every experiment, so they are only
        # right once each experiment's own derived data is. Registered separately rather than
        # as `rebuild_dashboard_data`, which is the two of them together, because they are
        # invalidated by different things -- a sample renumber changes the sample counts and
        # provably nothing about a mutation count, and `rebuild_after_structural_change` has
        # always refused to pay for the second. `only=` is how that refusal is now expressed.
        register_rebuilder('sample_counts', rebuild_sample_counts, scope=SITE_SCOPE,
                           label='Dashboard sample counts', priority=PRIORITY_AGGREGATE)
        # `inputs=INPUT_MUTATIONS`: the dashboard applies no filter, so a filter edit -- which
        # marks every experiment's derived data stale at once -- must not mark this. It is the
        # most expensive rebuild registered and the one a filter save has least business
        # invalidating. See `_live_observation_rows` for why the filter is not applied.
        register_rebuilder('mutation_counts', rebuild_mutation_counts, scope=SITE_SCOPE,
                           label='Dashboard mutation counts', priority=PRIORITY_AGGREGATE,
                           inputs={INPUT_MUTATIONS})

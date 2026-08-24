"""The curation actions every mutation table posts to.

These had **no tests at all**, which is how they came to have no authorization either:
`save_mut_tag` and `save_rep_tag` took a primary key and wrote, with no login check, no
project check and no experiment scoping. `LoginRequiredMiddleware` is only added in
`settings_private.py`, so a default deployment accepted them from an anonymous caller.

They are not Compare's -- Compare moved to the aledb-compare plugin and these stayed, because
Fixed Mutations, Converged Mutations and Search post here too. `@ajax` always sends HTTP 200
with the real status inside the JSON body, so the assertions below read `status` from the
body rather than from the response.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, Project, TechnicalReplicate,
)
from aledb_seq.models import Mutation

TAG_MUT = "/mutation-table/toggle-mut-tag/"
TAG_REP = "/mutation-table/toggle-rep-tag"


class TableActionsTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)
        # Through the view: it issues the guardian grant that can_add_experiment_filter
        # consults. Project.objects.create leaves the owner without it.
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        self.mutation = Mutation.objects.create(
            mutation_type="SNP", position=1000, sequence_change="A>T",
            ale_experiment=self.experiment)

        from aledb_import.gd_import import prepare_experiment_by_id
        context = prepare_experiment_by_id(self.experiment.ale_id)
        ale = AleId.objects.create(ale_experiment=self.experiment, ale_id=1)
        flask = Flask.objects.create(ale_id=ale, flask_number=1, media=context["media"])
        isolate = Isolate.objects.create(flask=flask, isolate_number=1, is_population=False,
                                         freezer_box=context["freezer_box"])
        self.replicate = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)

    # @ajax(mandatory=True) answers a plain POST with a bare 400 -- it requires the header
    # jQuery's $.ajax sets. Every call below goes through here so that is not re-learned.
    AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def _post(self, url, data):
        return self.client.post(url, data, **self.AJAX)

    def _tag_mutation(self, tag="contaminated"):
        return self._post(TAG_MUT, {"mut_id": self.mutation.id, "tag_name": tag})

    def _tag_replicate(self, tag="contaminated"):
        return self._post(TAG_REP, {"rep_id": self.replicate.id, "tag_name": tag})

    @staticmethod
    def _status(response):
        """@ajax reports the real status in the body; the envelope is always HTTP 200."""
        return response.json()["status"]

    # --- the routes live at the neutral prefix now ------------------------------------

    def test_the_endpoints_answer_on_the_new_prefix(self):
        """They used to be under /mutations/, which named a page three of their four
        callers were not -- and which core no longer serves at all."""
        self.assertEqual(200, self._tag_mutation().status_code)
        self.assertEqual(200, self._tag_replicate().status_code)

    def test_a_plain_post_is_refused(self):
        """@ajax(mandatory=True) answers anything without the XMLHttpRequest header with a
        bare 400. Pinned because it is the reason a curl reproduction of these endpoints
        looks broken when it is not."""
        self.assertEqual(
            400,
            self.client.post(TAG_MUT, {"mut_id": self.mutation.id,
                                       "tag_name": "contaminated"}).status_code)

    def test_the_old_mutations_paths_are_gone(self):
        for url in ("/mutations/toggle-mut-tag/", "/mutations/toggle-rep-tag",
                    "/mutations/add_to_exp_filter", "/mutations/add_to_global_filter"):
            with self.subTest(url=url):
                self.assertEqual(404, self._post(url, {}).status_code)

    # --- who may curate ---------------------------------------------------------------

    def test_the_owner_may_tag(self):
        self.assertEqual(200, self._status(self._tag_mutation()))
        self.mutation.refresh_from_db()
        self.assertEqual("contaminated", self.mutation.tags)

    def test_anonymous_may_not_tag_a_mutation(self):
        self.client.logout()

        self.assertEqual(403, self._status(self._tag_mutation()))

        self.mutation.refresh_from_db()
        self.assertFalse(self.mutation.tags)

    def test_anonymous_may_not_tag_a_replicate(self):
        self.client.logout()

        self.assertEqual(403, self._status(self._tag_replicate()))

        self.replicate.refresh_from_db()
        self.assertFalse(self.replicate.tags)

    def test_a_stranger_may_not_tag(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)

        self.assertEqual(403, self._status(self._tag_mutation()))
        self.assertEqual(403, self._status(self._tag_replicate()))

        self.mutation.refresh_from_db()
        self.assertFalse(self.mutation.tags)

    def test_staff_without_a_grant_may_not_tag(self):
        """can_add_experiment_filter consults the guardian grant, not the blanket staff
        clause that can_view_project carries -- so being staff is not enough."""
        staff = User.objects.create(username="staff", email="st@e.com",
                                    is_active=True, is_staff=True)
        self.client.force_login(staff)

        self.assertEqual(403, self._status(self._tag_mutation()))

    def test_a_superuser_may_tag_anything(self):
        admin = User.objects.create(username="admin", email="a@e.com",
                                    is_active=True, is_superuser=True)
        self.client.force_login(admin)

        self.assertEqual(200, self._status(self._tag_mutation()))
        self.assertEqual(200, self._status(self._tag_replicate()))

    def test_a_mutation_with_no_experiment_is_superuser_only(self):
        """It cannot be scoped to a project, so there is nothing to grant against."""
        loose = Mutation.objects.create(mutation_type="SNP", position=7,
                                        sequence_change="G>C", ale_experiment=None)

        response = self._post(TAG_MUT, {"mut_id": loose.id, "tag_name": "contaminated"})
        self.assertEqual(403, self._status(response))

        admin = User.objects.create(username="admin", email="a@e.com",
                                    is_active=True, is_superuser=True)
        self.client.force_login(admin)
        response = self._post(TAG_MUT, {"mut_id": loose.id, "tag_name": "contaminated"})
        self.assertEqual(200, self._status(response))

    # --- the toggle itself ------------------------------------------------------------

    def test_tagging_twice_removes_the_tag(self):
        self._tag_mutation()
        self._tag_mutation()

        self.mutation.refresh_from_db()
        self.assertEqual("", self.mutation.tags)

    def test_a_second_tag_joins_the_first(self):
        self._tag_mutation("contaminated")
        self._tag_mutation("hypermutated")

        self.mutation.refresh_from_db()
        # The storage format is the comma-joined string, so that is what is asserted --
        # a relation would not be able to lose a value to a stray comma, and this can.
        self.assertEqual(["contaminated", "hypermutated"], self.mutation.tags.split(","))

    def test_removing_one_of_two_leaves_the_other(self):
        self._tag_mutation("contaminated")
        self._tag_mutation("hypermutated")
        self._tag_mutation("contaminated")

        self.mutation.refresh_from_db()
        self.assertEqual("hypermutated", self.mutation.tags)

    def test_replicate_tags_toggle_the_same_way(self):
        self._tag_replicate("contaminated")
        self.replicate.refresh_from_db()
        self.assertEqual("contaminated", self.replicate.tags)

        self._tag_replicate("contaminated")
        self.replicate.refresh_from_db()
        self.assertEqual("", self.replicate.tags)


class CompareLeftCoreTestCase(TestCase):
    """What core gave up when Compare moved to the aledb-compare plugin."""

    def test_the_compare_route_is_gone(self):
        self.assertEqual(404, self.client.get("/mutations/").status_code)

    def test_the_per_sample_routes_still_resolve(self):
        """/mutations/ was the root of aledb_seq's include; its siblings are unaffected.

        Asserted through `resolve` rather than a request: `browse` answers 404 of its own
        accord without a mutation to open at, which would pass this test for the wrong
        reason."""
        from django.urls import Resolver404, resolve

        for url, view in (("/mutations/breseq", "breseq_table"),
                          ("/mutations/browse", "browse_mutation")):
            with self.subTest(url=url):
                try:
                    self.assertEqual(view, resolve(url).url_name)
                except Resolver404:
                    self.fail("%s no longer resolves" % url)

    def test_core_offers_no_compare_nav_entry(self):
        from aledb_common.nav_registry import EXPERIMENT_SECTION, get_nav_items

        labels = [item["label"] for item in get_nav_items(EXPERIMENT_SECTION)]
        self.assertNotIn("Compare", labels)


class SharedTableJsTestCase(TestCase):
    """`table_template.js` is a template, and nothing in core rendered it.

    It is `{% include %}`d by `base_table_template.html` (Compare, Fixed Mutations,
    Converged Mutations) and by the Search page -- none of which core's suite exercised:
    aledb_search has no tests, and the other three are plugin pages core cannot see. So the
    file could contain a broken template tag and the whole suite would still be green.

    That was theoretical while it held only literal strings. It is not any more: the three
    endpoint URLs are reversed by name now, so a rename breaks four pages at render time.
    These render it directly, which is the cheapest way for core to notice.
    """

    def _render(self):
        from django.template.loader import render_to_string

        return render_to_string("table_template.js", {})

    def test_the_shared_table_js_renders(self):
        """Catches any malformed tag in the file -- including one written inside a JS
        comment, which the template engine parses regardless of the //."""
        self.assertIn("function add_tag", self._render())

    def test_it_reverses_the_three_endpoints(self):
        js = self._render()

        self.assertIn("/mutation-table/toggle-mut-tag/", js)
        self.assertIn("/mutation-table/toggle-rep-tag", js)
        self.assertIn("/mutation-table/add_to_exp_filter", js)

    def test_no_endpoint_path_is_hardcoded_under_mutations(self):
        """The prefix these used to live under names a page core no longer serves."""
        self.assertNotIn("/mutations/toggle-mut-tag", self._render())
        self.assertNotIn("/mutations/add_to_exp_filter", self._render())

    def test_the_dead_global_filter_helper_is_gone(self):
        """Nothing emitted a call to it -- the menu item that did was removed from
        _build_table_cell_for_dropdown, leaving the function and its view unreachable."""
        self.assertNotIn("save_to_global_filter", self._render())

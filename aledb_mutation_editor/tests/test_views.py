"""The three pages render, and show the right things.

Asserted against the rendered HTML rather than the template files, because content placed
outside a `{% block %}` in a child template is silently discarded -- a script appended after
`{% endblock %}` never renders and nothing reports it.
"""

import json

from aledb_mutation_editor import history, validation
from aledb_mutation_editor.models import KIND_DELETE
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import ObservedMutation

EDIT = "/mutation-editor/"
ADD = "/mutation-editor/add"
COPY = "/mutation-editor/copy"
HISTORY = "/mutation-editor/history"


class PageTestCase(EditorTestCase):

    def get(self, url, **params):
        params.setdefault("ale_experiment_id", self.experiment.ale_id)
        return self.client.get(url, params)

    # --- the edit page --------------------------------------------------------------------

    def test_the_edit_page_lists_the_selected_samples_mutations(self):
        response = self.get(EDIT, reseq_id=self.sample_a.id)

        self.assertEqual(200, response.status_code)
        for observed in ObservedMutation.objects.filter(sequencing_experiment=self.sample_a):
            self.assertContains(response, 'data-observed-id="%d"' % observed.id)

    def test_it_does_not_list_another_samples_mutations(self):
        only_b = ObservedMutation.objects.get(sequencing_experiment=self.sample_b)
        response = self.get(EDIT, reseq_id=self.sample_a.id)
        self.assertNotContains(response, 'data-observed-id="%d"' % only_b.id)

    def test_it_falls_back_to_the_first_sample(self):
        """Opening the page from the sidebar carries no reseq_id."""
        response = self.get(EDIT)
        self.assertContains(response, 'data-observed-id=')

    def test_it_shows_mutations_the_experiment_filter_hides(self):
        """Filtering is a display concern; a filtered mutation is still stored.

        If the editor hid what the filter hides, there would be no way to delete it -- and it
        would reappear the moment somebody widened the filter.
        """
        response = self.get(EDIT, reseq_id=self.sample_a.id, ignore_genes="thrA")
        self.assertEqual(3, response.content.decode().count("data-observed-id="))

    def test_the_handler_guards_its_missing_control(self):
        """The button is absent for a reader, so the script must not assume it."""
        self.assertContains(
            self.get(EDIT),
            'if (!document.getElementById("me-apply")) { return; }')

    def test_the_delete_button_is_there_for_an_editor(self):
        self.assertContains(self.get(EDIT), 'id="me-apply"')

    def test_it_renders_a_csrf_token(self):
        """aledbPost reads the CSRF cookie, and the token tag is what sets it."""
        self.assertContains(self.get(EDIT), "csrfmiddlewaretoken")

    def test_it_does_not_promise_the_deletion_is_permanent(self):
        """aledbConfirmDelete says "This is permanent", which is the opposite of true here."""
        html = self.get(EDIT).content.decode()
        # base.html names the helper in a comment on every page, so look for the call.
        self.assertNotIn("aledbConfirmDelete(", html)
        self.assertIn("restore them from the history", html)

    # --- the copy page --------------------------------------------------------------------

    def test_the_copy_page_offers_the_other_samples_as_targets(self):
        response = self.get(COPY, source_reseq_id=self.sample_a.id)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'value="%d"' % self.sample_b.id)
        self.assertNotContains(response, 'class="me-target" value="%d"' % self.sample_a.id)

    def test_the_copy_page_lists_the_sources_mutations(self):
        response = self.get(COPY, source_reseq_id=self.sample_a.id)
        for mutation in (self.mut_1, self.mut_2, self.mut_3):
            self.assertContains(response, 'data-mutation-id="%d"' % mutation.id)

    # --- the add page ---------------------------------------------------------------------

    def test_the_add_page_renders_the_schema_for_the_client(self):
        """The dropdown, the visible fields and the server's required-field check all read
        one table, so it has to actually reach the page."""
        response = self.get(ADD)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'id="mutation-schema"')
        for name in validation.MUTATION_TYPES:
            self.assertContains(response, name)

    def test_the_add_page_offers_every_mutation_type(self):
        html = self.get(ADD).content.decode()
        for name in validation.MUTATION_TYPES:
            self.assertIn('<option value="%s">' % name, html)

    def test_it_renders_an_input_for_every_field_any_type_takes(self):
        """A field with no input would be invisible and unfillable for the types needing it."""
        html = self.get(ADD).content.decode()
        for entry in validation.form_schema()["types"]:
            for field in entry["fields"]:
                self.assertIn('data-name="%s"' % field, html,
                              "%s has no input, but %s needs it" % (field, entry["name"]))

    def test_it_lists_the_samples_to_add_to(self):
        response = self.get(ADD)
        for sample in (self.sample_a, self.sample_b):
            self.assertContains(response, 'class="me-target" value="%d"' % sample.id)

    def test_the_handler_guards_its_missing_control(self):
        self.assertContains(self.get(ADD),
                            'if (!document.getElementById("me-apply")) { return; }')

    # --- the history page -----------------------------------------------------------------

    def test_the_history_page_says_so_when_nothing_has_happened(self):
        response = self.get(HISTORY)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "No mutation edits have been recorded")

    def test_a_change_appears_on_the_history_page(self):
        observed = ObservedMutation.objects.get(sequencing_experiment=self.sample_a,
                                                mutation=self.mut_2)
        history.apply_changes(self.experiment, self.owner, KIND_DELETE, removals=[observed],
                              note="a note worth reading")

        response = self.get(HISTORY)
        self.assertContains(response, "a note worth reading")
        self.assertContains(response, "owner")
        self.assertContains(response, self.sample_a.ale_flask_isolate_str)

    def test_a_system_change_is_labelled_system_not_left_blank(self):
        observed = ObservedMutation.objects.get(sequencing_experiment=self.sample_a,
                                                mutation=self.mut_2)
        history.apply_changes(self.experiment, None, KIND_DELETE, removals=[observed],
                              note="migrated")

        self.assertContains(self.get(HISTORY), "<em>system</em>")

    def test_it_offers_a_restore_to_before_everything(self):
        """Only once there is something to come back from -- an experiment with no recorded
        edits is already in its imported state, so the row would be a button that does
        nothing."""
        self.assertNotContains(self.get(HISTORY), 'data-change-set-id=""')

        observed = ObservedMutation.objects.get(sequencing_experiment=self.sample_a,
                                                mutation=self.mut_2)
        history.apply_changes(self.experiment, self.owner, KIND_DELETE, removals=[observed])

        self.assertContains(self.get(HISTORY), 'data-change-set-id=""')

    # --- the shared strip -----------------------------------------------------------------

    def test_every_page_carries_the_experiment_through_its_links(self):
        """There is no session state holding the current experiment; a link that dropped it
        lands on the "pick an experiment first" page."""
        for url in (EDIT, COPY, HISTORY):
            with self.subTest(url=url):
                self.assertContains(
                    self.get(url),
                    "/mutation-editor/history?ale_experiment_id=%d" % self.experiment.ale_id)


class NoExperimentTestCase(EditorTestCase):

    def test_opening_a_page_with_no_experiment_is_not_an_error(self):
        """This is how these pages open from a bookmark, not a breakage."""
        for url in (EDIT, COPY, HISTORY):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(200, response.status_code)
                self.assertNotContains(response, "data-observed-id=")

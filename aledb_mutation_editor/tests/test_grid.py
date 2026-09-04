"""The whole experiment at once: `/mutation-editor/delete?sample_id=all`.

The grid is on both tabs, but selection is the Delete tab's -- so that is where these run. The
Edit tab renders the same rows with an `edit` link instead of a checkbox and no selection at
all, which `EditGridTestCase` at the foot covers.

The fixture is two samples and three mutations, and only `mut_1` is in both -- so a grid over
it has a row that is full, two that are half empty, and one column shorter than the other. That
asymmetry is the point: a builder that indexed cells by position rather than by sample would
line up on a fixture where every sample carried everything.
"""

import json

from aledb_mutation_editor.models import MutationChangeSet
from aledb_mutation_editor.tests.base import EditorTestCase
from aledb_seq.models import ObservedMutation

PAGE = "/mutation-editor/delete"


class GridPageTestCase(EditorTestCase):

    def grid(self, **params):
        params.setdefault("experiment_id", self.experiment.id)
        params.setdefault("sample_id", "all")
        return self.client.get(PAGE, params)

    # --- what it renders ------------------------------------------------------------------

    def test_it_renders_every_sample_as_a_column(self):
        html = self.grid().content.decode("utf-8")

        for sample in (self.sample_a, self.sample_b):
            self.assertIn('data-sample="%d"' % sample.id, html)

    def test_it_renders_every_mutation_as_a_row(self):
        html = self.grid().content.decode("utf-8")

        for mutation in (self.mut_1, self.mut_2, self.mut_3):
            self.assertIn('data-mutation="%d"' % mutation.id, html)

    def test_every_observation_is_addressable_by_its_own_id(self):
        """The cell is what gets selected, so each one carries the observation's id -- not the
        mutation's, which is in two samples and would delete both."""
        html = self.grid().content.decode("utf-8")

        for observed in ObservedMutation.objects.all():
            self.assertIn('data-obs="%d"' % observed.id, html)

    def test_a_sample_that_does_not_carry_a_mutation_gets_an_empty_cell(self):
        """sample_b has only mut_1. Its cells for mut_2 and mut_3 have to be empty rather
        than absent, or every row after them shifts a column left."""
        html = self.grid().content.decode("utf-8")

        # An empty cell is a bare <td>: it has to be there for alignment, and it
        # carries no id because there is nothing to select.
        self.assertIn("<td></td>", html)

    def test_the_per_sample_table_is_not_rendered_as_well(self):
        """Both modes define a Delete button; only one table may be on the page, or the
        per-sample handler binds to the grid's button on top of the grid's own."""
        html = self.grid().content.decode("utf-8")

        self.assertNotIn('id="me-table"', html)
        self.assertIn('id="me-grid"', html)

    def test_picking_a_sample_still_renders_the_per_sample_table(self):
        response = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                          "sample_id": self.sample_a.id})
        html = response.content.decode("utf-8")

        self.assertIn('id="me-table"', html)
        self.assertNotIn('id="me-grid"', html)

    def test_all_samples_is_not_the_default(self):
        """Arriving from a sample should not land on a page that lays out every mutation
        against every sample."""
        response = self.client.get(PAGE, {"experiment_id": self.experiment.id})

        self.assertIn('id="me-table"', response.content.decode("utf-8"))

    # --- the maps the selectors read ------------------------------------------------------

    def test_the_row_map_lists_every_sample_carrying_that_mutation(self):
        """`deferRender` leaves undrawn cells with no DOM, so 'select this mutation
        everywhere' is answered from this map rather than by walking the table."""
        html = self.grid().content.decode("utf-8")
        by_mutation = json.loads(self._json_script(html, "me-by-mutation"))

        both = {observed.id for observed in
                ObservedMutation.objects.filter(mutation=self.mut_1)}
        self.assertEqual(both, set(by_mutation[str(self.mut_1.id)]))
        self.assertEqual(2, len(both), "mut_1 is the one in both samples")

    def test_the_column_map_lists_every_mutation_in_that_sample(self):
        html = self.grid().content.decode("utf-8")
        by_sample = json.loads(self._json_script(html, "me-by-sample"))

        self.assertEqual(3, len(by_sample[str(self.sample_a.id)]))
        self.assertEqual(1, len(by_sample[str(self.sample_b.id)]))

    def _json_script(self, html, element_id):
        marker = '<script id="%s" type="application/json">' % element_id
        start = html.index(marker) + len(marker)
        return html[start:html.index("</script>", start)]

    # --- deleting across samples ----------------------------------------------------------

    def test_one_post_across_two_samples_is_one_changeset(self):
        """The whole reason for the mode. It was one page load per sample before, and one
        history entry per sample with it."""
        ids = [observed.id for observed in
               ObservedMutation.objects.filter(mutation=self.mut_1)]
        self.assertEqual(2, len(ids))

        response = self.client.post("/mutation-editor/delete/apply", {
            "experiment_id": self.experiment.id,
            "observed_ids": json.dumps(ids)})

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()["removed"])
        self.assertEqual(1, MutationChangeSet.objects.count())
        self.assertFalse(ObservedMutation.objects.filter(mutation=self.mut_1).exists())

    def test_the_mutation_row_itself_survives_the_delete(self):
        """Deleting every observation of a mutation must not delete the Mutation: its
        primary key is stored as a bare integer in aledb-converge, aledb-phylogeny and every
        exported CSV."""
        ids = [observed.id for observed in
               ObservedMutation.objects.filter(mutation=self.mut_1)]

        self.client.post("/mutation-editor/delete/apply", {
            "experiment_id": self.experiment.id,
            "observed_ids": json.dumps(ids)})

        self.mut_1.refresh_from_db()
        self.assertIsNotNone(self.mut_1.pk)

    def test_a_selection_spanning_samples_and_mutations_deletes_exactly_it(self):
        """Not a whole row and not a whole column -- the arbitrary set a person clicks."""
        keep = ObservedMutation.objects.get(sample=self.sample_a,
                                            mutation=self.mut_2)
        doomed = [ObservedMutation.objects.get(sample=self.sample_a,
                                              mutation=self.mut_1).id,
                  ObservedMutation.objects.get(sample=self.sample_b,
                                               mutation=self.mut_1).id,
                  ObservedMutation.objects.get(sample=self.sample_a,
                                               mutation=self.mut_3).id]

        self.client.post("/mutation-editor/delete/apply", {
            "experiment_id": self.experiment.id,
            "observed_ids": json.dumps(doomed)})

        self.assertEqual([keep.id],
                         list(ObservedMutation.objects.values_list("id", flat=True)))

    def test_an_observation_from_another_experiment_is_refused(self):
        """The endpoint scopes ids through the experiment, so a hand-built POST cannot reach
        across projects even with the grid handing it a longer list."""
        from aledb_experiment.models import Experiment, Population, TimePoint
        from aledb_import.gd_import import prepare_experiment_by_id
        from aledb_seq.models import Sample

        created = self.client.post(
            "/project/create/", {"name": "P2", "experiment": "E2"}).json()
        other = Experiment.objects.get(pk=created["experiment_id"])
        context = prepare_experiment_by_id(other.id)
        ale = Population.objects.create(experiment=other, name=1)
        flask = TimePoint.objects.create(population=ale, value=1, media=context["media"])
        sample = Sample.objects.create(
            time_point=flask, name=1, is_clonal=True, source_name="x")
        outside = ObservedMutation.objects.create(
            sample=sample,
            mutation=self.make_mutation(position=999, sequence_change="T>A",
                                        experiment=other),
            present=True)

        response = self.client.post("/mutation-editor/delete/apply", {
            "experiment_id": self.experiment.id,
            "observed_ids": json.dumps([outside.id])})

        self.assertEqual(404, response.status_code)
        self.assertTrue(ObservedMutation.objects.filter(pk=outside.pk).exists())

    def test_the_grid_shows_only_this_experiment(self):
        """The other half of the same scoping: another experiment's observation must not
        appear as a selectable cell in the first place."""
        created = self.client.post(
            "/project/create/", {"name": "P3", "experiment": "E3"}).json()
        from aledb_experiment.models import Experiment

        other = Experiment.objects.get(pk=created["experiment_id"])
        stranger = self.make_mutation(position=4242, sequence_change="G>C", experiment=other)

        html = self.grid().content.decode("utf-8")

        self.assertNotIn('data-mutation="%d"' % stranger.id, html)

    # --- a mutation with nothing left in it -----------------------------------------------

    def test_deleting_the_last_observation_removes_the_row(self):
        """The reported bug. The page reloads; the row was genuinely still being rendered.

        `Mutation` rows are never deleted here -- their ids are stored as bare integers in
        aledb-converge, aledb-phylogeny and every exported CSV -- so a mutation whose last
        observation goes still exists, and a grid built from `Mutation.objects.filter(...)`
        renders it with every cell empty.
        """
        ids = [observed.id for observed in
               ObservedMutation.objects.filter(mutation=self.mut_1)]

        self.client.post("/mutation-editor/delete/apply", {
            "experiment_id": self.experiment.id,
            "observed_ids": json.dumps(ids)})

        html = self.grid().content.decode("utf-8")
        self.assertNotIn('data-mutation="%d"' % self.mut_1.id, html)
        self.assertIn('data-mutation="%d"' % self.mut_2.id, html,
                      "the mutations that still have observations are untouched")

    def test_a_mutation_no_sample_observes_is_not_listed(self):
        """The same state arrived at without a delete -- an import can leave one, and
        experiment 2 in the dev database carries one today."""
        orphan = self.make_mutation(position=8888, sequence_change="T>G")

        html = self.grid().content.decode("utf-8")

        self.assertNotIn('data-mutation="%d"' % orphan.id, html)

    def test_the_total_counts_only_what_could_be_shown(self):
        """`Showing N of M` is a promise that the other M-N are reachable by searching. An
        orphan is reachable by nothing: there is no cell on its row to select.

        Read off the view's own context rather than recomputed here, which would just be the
        fix written twice and would pass whatever the page did.
        """
        self.make_mutation(position=8888, sequence_change="T>G")

        response = self.grid()

        self.assertEqual(4, self.experiment.mutations.count(), "the orphan is stored")
        self.assertEqual(3, response.context["grid_total"])
        self.assertEqual(3, len(response.context["grid_rows"]))

    # --- the column header is a control, so it says what it would do -----------------------

    def test_a_sample_with_nothing_on_the_page_is_not_a_selector(self):
        """Measured in a browser first: an experiment's mutations are often concentrated in
        a subset of its samples, so several column headers select nothing. A control that
        looks live and does nothing when clicked reads as broken."""
        empty = self.make_sample(flask_number=99)

        html = self.grid().content.decode("utf-8")

        self.assertIn('data-sample="%d"' % self.sample_a.id, html)
        self.assertNotIn('data-sample="%d"' % empty.id, html)
        self.assertIn(empty.label, html)

    def test_the_header_says_how_much_it_would_select(self):
        html = self.grid().content.decode("utf-8")

        self.assertIn("Select all 3 in this sample", html)   # sample_a has all three
        self.assertIn("Select all 1 in this sample", html)   # sample_b has only mut_1

    # --- narrowing ------------------------------------------------------------------------

    def test_searching_narrows_which_mutations_are_built(self):
        """Server-side, not DataTables'. A client-side search still ships every row, and the
        largest experiment here renders to 32.8 MB unnarrowed."""
        self.mut_2.gene = "ilvG"
        self.mut_2.save()

        html = self.grid(q="ilvG").content.decode("utf-8")

        self.assertIn('data-mutation="%d"' % self.mut_2.id, html)
        self.assertNotIn('data-mutation="%d"' % self.mut_1.id, html)

    def test_a_numeric_search_matches_the_position_exactly(self):
        """A substring match on a coordinate is never what anybody means -- searching 100
        must not also return 1000 and 2100."""
        far = self.make_mutation(position=1002, sequence_change="T>C")
        self.observe(self.sample_a, far)

        html = self.grid(q="100").content.decode("utf-8")

        self.assertIn('data-mutation="%d"' % self.mut_1.id, html)
        self.assertNotIn('data-mutation="%d"' % far.id, html)

    def test_a_search_matching_nothing_says_so(self):
        html = self.grid(q="nosuchgene").content.decode("utf-8")

        self.assertIn("No mutation matches", html)

    def test_the_maps_cover_only_what_was_rendered(self):
        """They could just as easily cover the whole experiment. Then a column selector would
        put observations a person cannot see into a selection whose next button deletes
        them."""
        self.mut_2.gene = "ilvG"
        self.mut_2.save()

        html = self.grid(q="ilvG").content.decode("utf-8")
        by_mutation = json.loads(self._json_script(html, "me-by-mutation"))

        self.assertEqual([str(self.mut_2.id)], list(by_mutation))


class EditGridTestCase(EditorTestCase):
    """The same grid under the Edit tab, which offers a link rather than a selection."""

    def grid(self):
        return self.client.get("/mutation-editor/", {
            "experiment_id": self.experiment.id, "sample_id": "all"})

    def test_it_offers_an_edit_link_per_mutation(self):
        html = self.grid().content.decode("utf-8")

        for mutation in (self.mut_1, self.mut_2, self.mut_3):
            self.assertIn("mutation_id=%d" % mutation.id, html)

    def test_it_has_no_selection_at_all(self):
        """Not merely a hidden button: the checkbox column, the cell ids and the two maps are
        all absent, so there is nothing on the page a stray click could select."""
        html = self.grid().content.decode("utf-8")

        self.assertNotIn('class="me-row-box"', html)
        self.assertNotIn('id="me-apply"', html)
        self.assertNotIn('id="me-by-mutation"', html)

    def test_the_delete_tab_still_has_them(self):
        """The counterpart, so a mode that rendered nothing anywhere would not pass the two
        assertions above by being broken."""
        html = self.client.get(PAGE, {"experiment_id": self.experiment.id,
                                      "sample_id": "all"}).content.decode("utf-8")

        self.assertIn('class="me-row-box"', html)
        self.assertIn('id="me-by-mutation"', html)

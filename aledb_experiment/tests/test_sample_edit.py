"""Editing a sample's identity and description.

The thing worth testing here is not that a field saves -- it is that saving one sample
leaves its siblings alone. A sample's A/F/I/R coordinate lives in four rows *above* it,
every one of them shared, so the naive implementation (write the number onto the row)
silently renumbers every other sample under the same flask. Half of what follows exists to
pin that down.
"""

import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from aledb_experiment.models import (
    AleExperiment, AleId, Flask, Isolate, Project, TechnicalReplicate,
)
from aledb_seq.models import ResequencingExperiment
from aledb_experiment import paths


class SampleEditTestCase(TestCase):
    """Shared fixture: an owner, a project, an experiment, and a sample factory."""

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        # Through the view, not Project.objects.create: the latter leaves the owner
        # without the django-guardian grant and every page then 403s.
        created = self.client.post(
            "/ale/projects/create/", {"name": "P", "experiment": "E"}).json()
        self.project = Project.objects.get(pk=created["project_id"])
        self.experiment = AleExperiment.objects.get(pk=created["experiment_id"])

        from aledb_import.gd_import import prepare_experiment_by_id
        # Never _prepare_experiment: its name-based lookup reaches find_user, which
        # prompts on stdin and raises EOFError under the test runner.
        context = prepare_experiment_by_id(self.experiment.id)
        self.media = context["media"]

    def make_sample(self, ale, flask, isolate, rep, name="", experiment=None,
                    media=None, is_population=False):
        experiment = experiment or self.experiment
        ale_row, _ = AleId.objects.get_or_create(
            ale_experiment=experiment, ale_id=ale)
        flask_row, _ = Flask.objects.get_or_create(
            ale_id=ale_row, flask_number=flask,
            defaults={"media": media or self.media})
        isolate_row = Isolate.objects.create(
            flask=flask_row, isolate_number=isolate, is_population=is_population)
        tech_rep = TechnicalReplicate.objects.create(
            isolate=isolate_row, tech_rep_number=rep)
        return ResequencingExperiment.objects.create(
            tech_rep=tech_rep, sample_name=name)

    def row(self, reseq, **overrides):
        from aledb_experiment.samples import sample_coordinate
        ale, flask, isolate, rep = sample_coordinate(reseq)
        row = {"id": str(reseq.pk), "sample_name": reseq.sample_name or "",
               "ale": ale, "flask": flask,
               "isolate": isolate, "rep": rep,
               "is_population": 1 if reseq.tech_rep.isolate.is_population else 0,
               "isolate_description": reseq.tech_rep.isolate.description or ""}
        row.update(overrides)
        return row

    def bulk(self, rows, experiment=None):
        experiment = experiment or self.experiment
        return self.client.post(
            "/ale/experiment/%d/samples/update/" % experiment.id,
            {"rows": json.dumps(rows)})

    def single(self, reseq, **overrides):
        data = self.row(reseq, **overrides)
        data.pop("id")
        return self.client.post("/ale/sample/%d/update/" % reseq.pk, data)

    def coordinate(self, reseq):
        from aledb_experiment.samples import sample_coordinate
        reseq.refresh_from_db()
        return sample_coordinate(reseq)

    def chain_counts(self):
        return (AleId.objects.count(), Flask.objects.count(),
                Isolate.objects.count(), TechnicalReplicate.objects.count())


class SampleEditPagesTestCase(SampleEditTestCase):
    def setUp(self):
        super().setUp()
        self.sample = self.make_sample(1, 1, 1, 1, name="first")

    def test_the_sample_page_renders(self):
        response = self.client.get("/ale/sample/%d/edit/" % self.sample.pk)
        self.assertEqual(200, response.status_code)
        for field in ('id="se-name"', 'id="se-ale"', 'id="se-flask"',
                      'id="se-isolate"', 'id="se-rep"', 'id="se-save"'):
            self.assertContains(response, field)

    def test_the_bulk_page_lists_this_experiment_only(self):
        other_project = Project.objects.get(
            pk=self.client.post("/ale/projects/create/",
                                {"name": "Q", "experiment": "F"}).json()["project_id"])
        other = AleExperiment.objects.filter(project=other_project).first()
        stranger_sample = self.make_sample(1, 1, 1, 1, name="elsewhere", experiment=other)

        html = self.client.get(
            "/ale/experiment/%d/samples/" % self.experiment.id).content.decode()

        self.assertIn('data-reseq-id="%d"' % self.sample.pk, html)
        self.assertNotIn('data-reseq-id="%d"' % stranger_sample.pk, html)

    def test_the_pages_show_the_coordinate_beside_the_displayed_label(self):
        """A renumber can change no visible label at all: ale_flask_isolate_str returns
        the isolate description whenever it is set, and the import path fills it with the
        filename. Both pages must show the numbers as well, or a successful save looks
        like it did nothing."""
        isolate = self.sample.tech_rep.isolate
        isolate.description = "Ara-1_500gen_762B"
        isolate.save()

        for url in ("/ale/sample/%d/edit/" % self.sample.pk,
                    "/ale/experiment/%d/samples/" % self.experiment.id):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn("Ara-1_500gen_762B", html)
                self.assertIn("A1 F1 I1 R1", html)

    def test_signed_out_they_are_forbidden(self):
        self.client.logout()
        for url in ("/ale/sample/%d/edit/" % self.sample.pk,
                    "/ale/experiment/%d/samples/" % self.experiment.id):
            with self.subTest(url=url):
                self.assertEqual(403, self.client.get(url).status_code)

    def test_staff_may_view_but_not_edit(self):
        """can_view_project lets every staff user through; can_edit_project does not."""
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True, is_staff=True)
        self.client.force_login(stranger)
        for url in ("/ale/sample/%d/edit/" % self.sample.pk,
                    "/ale/experiment/%d/samples/" % self.experiment.id):
            with self.subTest(url=url):
                self.assertEqual(403, self.client.get(url).status_code)

    def test_every_handler_guards_its_missing_control(self):
        for url, control in (("/ale/sample/%d/edit/" % self.sample.pk, "se-save"),
                             ("/ale/experiment/%d/samples/" % self.experiment.id,
                              "sb-save")):
            with self.subTest(url=url):
                self.assertContains(
                    self.client.get(url),
                    'if (!document.getElementById("%s")) { return; }' % control)

    def test_neither_is_a_modal_and_both_offer_a_way_back(self):
        for url in ("/ale/sample/%d/edit/" % self.sample.pk,
                    "/ale/experiment/%d/samples/" % self.experiment.id):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, 'data-toggle="modal"')
                self.assertContains(response, "Cancel")

    def test_a_sample_with_no_tech_rep_is_a_404(self):
        """It has no project, so it cannot be permission-checked. Repairing those belongs
        in a management command, not in a page that would have to skip the check."""
        orphan = ResequencingExperiment.objects.create(tech_rep=None, sample_name="loose")
        self.assertEqual(404, self.client.get("/ale/sample/%d/edit/" % orphan.pk).status_code)


class DescriptiveEditTestCase(SampleEditTestCase):
    def setUp(self):
        super().setUp()
        self.sample = self.make_sample(1, 1, 1, 1, name="first")

    def test_renaming_a_sample_saves(self):
        response = self.single(self.sample, sample_name="renamed")

        self.assertEqual(200, response.status_code, response.content)
        self.sample.refresh_from_db()
        self.assertEqual("renamed", self.sample.sample_name)

    def test_no_edit_page_can_change_who_the_sample_belongs_to(self):
        """Changing an owner is its own workflow. The endpoint builds the row itself, so
        a `person` in the POST is not merely ignored -- there is nowhere for it to go."""
        self.sample.person = "original"
        self.sample.save()

        payload = self.row(self.sample, sample_name="renamed", person="impostor")
        payload.pop("id")
        response = self.client.post("/ale/sample/%d/update/" % self.sample.pk, payload)

        self.assertEqual(200, response.status_code, response.content)
        self.sample.refresh_from_db()
        self.assertEqual("renamed", self.sample.sample_name)
        self.assertEqual("original", self.sample.person)

    def test_a_bulk_save_leaves_person_alone(self):
        self.sample.person = "original"
        self.sample.save()

        self.assertEqual(200, self.bulk([self.row(self.sample, ale=3)]).status_code)

        self.sample.refresh_from_db()
        self.assertEqual("original", self.sample.person)

    def test_the_isolate_description_saves(self):
        self.single(self.sample, isolate_description="colony from day 30")

        self.sample.refresh_from_db()
        self.assertEqual("colony from day 30", self.sample.tech_rep.isolate.description)

    def test_a_descriptive_save_creates_and_deletes_no_chain_rows(self):
        before = self.chain_counts()
        self.single(self.sample, sample_name="renamed")
        self.assertEqual(before, self.chain_counts())

    def test_the_bulk_table_does_not_blank_fields_it_has_no_column_for(self):
        """It shows no replicate description; a missing key must leave the value alone."""
        tech_rep = self.sample.tech_rep
        tech_rep.description = "second run"
        tech_rep.tags = "resequenced"
        tech_rep.save()

        self.assertEqual(200, self.bulk([self.row(self.sample, ale=2)]).status_code)

        self.sample.refresh_from_db()
        self.assertEqual("second run", self.sample.tech_rep.description)
        self.assertEqual("resequenced", self.sample.tech_rep.tags)

    def test_a_duplicate_sample_name_is_refused(self):
        """Re-import finds an existing sample by name within the experiment, so two
        sharing one means a later upload can attach to the wrong sample."""
        self.make_sample(1, 1, 2, 1, name="second")

        response = self.single(self.sample, sample_name="second")

        self.assertEqual(400, response.status_code)
        self.sample.refresh_from_db()
        self.assertEqual("first", self.sample.sample_name)

    def test_an_over_long_value_is_refused_rather_than_truncated(self):
        response = self.single(self.sample, sample_name="x" * 201)

        self.assertEqual(400, response.status_code)
        self.sample.refresh_from_db()
        self.assertEqual("first", self.sample.sample_name)


class RenumberTestCase(SampleEditTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.make_sample(1, 1, 1, 1, name="first")
        self.second = self.make_sample(1, 1, 2, 1, name="second")

    def test_renumbering_one_sample_leaves_its_sibling_alone(self):
        """The whole reason identity is changed by re-pointing the FK. AleId, Flask,
        Isolate and TechnicalReplicate rows are shared, so writing a number onto one
        renumbers every sample beneath it."""
        sibling_tech_rep = self.second.tech_rep_id

        self.assertEqual(200, self.single(self.first, ale=2).status_code)

        self.assertEqual(("2", 1, "1", 1), self.coordinate(self.first))
        self.assertEqual(("1", 1, "2", 1), self.coordinate(self.second))
        self.second.refresh_from_db()
        self.assertEqual(sibling_tech_rep, self.second.tech_rep_id)

    def test_the_sample_keeps_its_primary_key(self):
        """The store keys BAM and BigWig paths by pk, so a renumber must not move files."""
        original = self.first.pk

        self.assertEqual(200, self.single(self.first, ale=7, flask=9).status_code)

        self.assertEqual(("7", 9, "1", 1), self.coordinate(self.first))
        self.assertEqual(original, self.first.pk)
        self.assertTrue(ResequencingExperiment.objects.filter(pk=original).exists())

    def test_a_fresh_coordinate_creates_the_rows_it_needs(self):
        """One of each is created, and whatever the move emptied is pruned in the same
        step -- so ALE and flask grow by one (ALE 1 and flask 1 still hold the sibling)
        while isolate and replicate come out level, having been created and vacated."""
        before = self.chain_counts()
        self.single(self.first, ale=5, flask=5, isolate=5, rep=5)
        ales, flasks, isolates, reps = self.chain_counts()

        self.assertEqual(before[0] + 1, ales)
        self.assertEqual(before[1] + 1, flasks)
        self.assertEqual(before[2], isolates)
        self.assertEqual(before[3], reps)
        self.assertTrue(Isolate.objects.filter(
            **{paths.to_ale_label(root="isolate"): 5, "isolate_number": 5}).exists())
        self.assertFalse(Isolate.objects.filter(
            **{paths.to_ale_label(root="isolate"): 1, "isolate_number": 1}).exists())

    def test_the_new_rows_inherit_media_from_the_source(self):
        """A renumber re-labels a sample; it does not move it to different growth
        conditions.

        This asserted the freezer box as well, until that model was deleted: every column on
        it was either dead or a write-only placeholder, so it was a required foreign key to a
        singleton row nothing ever displayed."""
        from aledb_experiment.models import Media
        other_media = Media.objects.create(description="LB")
        flask = self.first.tech_rep.isolate.flask
        flask.media = other_media
        flask.save()

        self.single(self.first, ale=4)

        self.first.refresh_from_db()
        self.assertEqual(other_media, self.first.tech_rep.isolate.flask.media)

    def test_moving_onto_a_flask_with_different_media_succeeds(self):
        """gd_import passes media= as a *lookup* kwarg to Flask.objects.get_or_create
        while Flask is unique on (ale_id, flask_number), so it raises IntegrityError
        against an existing flask carrying different media. Keying on the unique tuple is
        what makes this work."""
        from aledb_experiment.models import Media
        self.make_sample(3, 3, 1, 1, name="third",
                         media=Media.objects.create(description="LB"))

        response = self.single(self.first, ale=3, flask=3, isolate=9)

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(("3", 3, "9", 1), self.coordinate(self.first))

    def test_two_isolate_rows_at_one_number_do_not_break_the_lookup(self):
        """Isolate has no unique_together and gd_import get_or_creates it on six fields,
        so duplicates at one (flask, isolate_number) already exist in real data.
        get_or_create would raise MultipleObjectsReturned on them."""
        flask = self.second.tech_rep.isolate.flask
        Isolate.objects.create(flask=flask, isolate_number=2, is_population=True,
                               reseq_date="2020-01-01")

        response = self.single(self.first, isolate=2, rep=4)

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(("1", 1, "2", 4), self.coordinate(self.first))

    def test_the_population_flag_toggles(self):
        self.single(self.first, is_population=1)
        self.first.refresh_from_db()
        self.assertTrue(self.first.tech_rep.isolate.is_population)

    def test_a_named_ale_is_accepted(self):
        """The whole point of the text columns: `Ara-1` and `Ara+1` are two populations."""
        self.assertEqual(200, self.single(self.first, ale="Ara+1").status_code)
        self.assertEqual(("Ara+1", 1, "1", 1), self.coordinate(self.first))

    def test_and_so_is_a_named_isolate(self):
        self.assertEqual(200, self.single(self.first, isolate="763A").status_code)
        self.assertEqual(("1", 1, "763A", 1), self.coordinate(self.first))

    def test_a_non_integer_time_point_is_still_refused(self):
        """The flask is the one member of the coordinate that has to be a number."""
        response = self.single(self.first, flask="five hundred")
        self.assertEqual(400, response.status_code)
        self.assertIn("time point must be a whole number", response.json()["error"])
        self.assertEqual(("1", 1, "1", 1), self.coordinate(self.first))

    def test_a_blank_ale_is_refused(self):
        """A sample has to sit somewhere, and `strip()` makes a space blank."""
        self.assertEqual(400, self.single(self.first, ale="  ").status_code)
        self.assertEqual(("1", 1, "1", 1), self.coordinate(self.first))

    def test_an_over_long_label_is_refused_rather_than_truncated(self):
        response = self.single(self.first, isolate="x" * 101)
        self.assertEqual(400, response.status_code)
        self.assertIn("too long", response.json()["error"])

    def test_surrounding_space_is_trimmed_from_a_label(self):
        """Two coordinates that read identically must not point at different rows."""
        self.assertEqual(200, self.single(self.first, ale=" Ara-1 ").status_code)
        self.assertEqual(("Ara-1", 1, "1", 1), self.coordinate(self.first))

    def test_a_negative_number_is_refused(self):
        self.assertEqual(400, self.single(self.first, flask=-1).status_code)

    def test_ale_zero_is_allowed(self):
        """STARTING_STRAIN_ALE_ID is "0", and the starting strain is a real sample."""
        self.assertEqual(200, self.single(self.first, ale=0).status_code)
        self.assertEqual(("0", 1, "1", 1), self.coordinate(self.first))

    def test_moving_onto_an_occupied_coordinate_is_refused(self):
        response = self.single(self.first, isolate=2)

        self.assertEqual(400, response.status_code)
        self.assertIn("cannot share one identity", response.json()["error"])
        self.assertEqual(("1", 1, "1", 1), self.coordinate(self.first))
        self.assertEqual(("1", 1, "2", 1), self.coordinate(self.second))

    def test_a_non_owner_cannot_edit(self):
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True, is_staff=True)
        self.client.force_login(stranger)
        self.assertEqual(403, self.single(self.first, ale=2).status_code)


class OrphanPruningTestCase(SampleEditTestCase):
    def setUp(self):
        super().setUp()
        self.only = self.make_sample(1, 1, 1, 1, name="only")

    def test_moving_the_last_sample_out_removes_the_rows_it_emptied(self):
        """Empty rows are not neutral: rebuild_sample_counts counts AleId/Flask/Isolate
        rows, and the ALE picker is built from AleId rows."""
        self.single(self.only, ale=2, flask=2, isolate=2)

        self.assertFalse(AleId.objects.filter(
            ale_experiment=self.experiment, ale_id=1).exists())
        self.assertEqual(1, Flask.objects.count())
        self.assertEqual(1, Isolate.objects.count())
        self.assertEqual(1, TechnicalReplicate.objects.count())

    def test_a_flask_that_still_holds_another_isolate_survives(self):
        self.make_sample(1, 1, 2, 1, name="sibling")

        self.single(self.only, isolate=5)

        self.assertTrue(Flask.objects.filter(
            **{paths.to_experiment(root="flask"): self.experiment,
               "flask_number": 1}).exists())
        self.assertTrue(AleId.objects.filter(
            ale_experiment=self.experiment, ale_id=1).exists())

    # Two tests stood here, and both pinned orphan guards against columns nothing wrote.
    # `AleId.starting_strain` went first: a FK to Isolate no code path ever set, replaced by
    # `AleExperiment.ancestor`, which points at a sample. `Isolate.parent_isolate` has now
    # gone the same way -- also never written, so the state its guard protected against could
    # not arise. Neither the columns nor the guards remain.


class BulkSampleEditTestCase(SampleEditTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.make_sample(1, 1, 1, 1, name="first")
        self.second = self.make_sample(1, 2, 1, 1, name="second")

    def test_a_swap_succeeds_in_one_save(self):
        """A target held by a sample that is itself moving away in the same batch is not a
        collision -- refusing it would make the commonest bulk operation impossible."""
        response = self.bulk([self.row(self.first, flask=2),
                              self.row(self.second, flask=1)])

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(("1", 2, "1", 1), self.coordinate(self.first))
        self.assertEqual(("1", 1, "1", 1), self.coordinate(self.second))
        self.assertEqual(2, Flask.objects.count())
        self.assertEqual(2, Isolate.objects.count())

    def test_one_bad_row_saves_nothing(self):
        response = self.bulk([self.row(self.first, sample_name="renamed"),
                              self.row(self.second, flask="not a number")])

        self.assertEqual(400, response.status_code)
        self.assertIn(str(self.second.pk), response.json()["errors"])
        self.first.refresh_from_db()
        self.assertEqual("first", self.first.sample_name)

    def test_two_rows_claiming_one_coordinate_are_refused(self):
        response = self.bulk([self.row(self.first, flask=3),
                              self.row(self.second, flask=3)])

        self.assertEqual(400, response.status_code)
        self.assertEqual(("1", 1, "1", 1), self.coordinate(self.first))
        self.assertEqual(("1", 2, "1", 1), self.coordinate(self.second))

    def test_a_sample_from_another_experiment_is_refused(self):
        created = self.client.post(
            "/ale/projects/create/", {"name": "Q", "experiment": "F"}).json()
        other = AleExperiment.objects.get(pk=created["experiment_id"])
        foreign = self.make_sample(1, 1, 1, 1, name="foreign", experiment=other)

        response = self.bulk([self.row(foreign, flask=8)])

        self.assertEqual(400, response.status_code)
        self.assertEqual(("1", 1, "1", 1), self.coordinate(foreign))

    def test_malformed_rows_is_a_400_not_a_500(self):
        response = self.client.post(
            "/ale/experiment/%d/samples/update/" % self.experiment.id,
            {"rows": "not json"})
        self.assertEqual(400, response.status_code)

    def test_rows_must_be_a_list(self):
        response = self.client.post(
            "/ale/experiment/%d/samples/update/" % self.experiment.id,
            {"rows": json.dumps({"id": "1"})})
        self.assertEqual(400, response.status_code)

    def test_a_non_owner_cannot_save(self):
        stranger = User.objects.create(
            username="stranger", email="s@e.com", is_active=True, is_staff=True)
        self.client.force_login(stranger)
        self.assertEqual(403, self.bulk([self.row(self.first, flask=9)]).status_code)


class RebuildHooksTestCase(SampleEditTestCase):
    """What a save recomputes, and what it deliberately does not.

    Fixation reads the flask numbers directly, so it has to rebuild. Mutation counts
    cannot depend on a sample's identity, and rebuild_mutation_counts pulls every
    ObservedMutation in the database into Python -- paying that on every rename is the one
    thing here that could make the feature feel broken in production.
    """

    def setUp(self):
        super().setUp()
        self.first = self.make_sample(1, 1, 1, 1, name="first")
        self.second = self.make_sample(1, 1, 2, 1, name="second")

    def _patched(self):
        """Patch the bus, not the individual rebuilds.

        `rebuild_registry` holds the callables themselves -- registered from AppConfig.ready()
        -- so patching `aledb_dashboard.util.rebuild_sample_counts` would replace a module
        attribute the registry no longer reads, and the assertion would pass for the wrong
        reason. Every other registry in the suite stores callables the same way. What this
        class is really about is *which* rebuilds a structural save asks for, and that is now
        one argument on one call, so assert on it directly.
        """
        return (mock.patch("aledb_common.rebuild_registry.run_rebuilds"),
                mock.patch("aledb_common.rebuild_registry.request_rebuild"))

    def test_a_structural_save_rebuilds_once_for_the_whole_batch(self):
        run, request = self._patched()
        with run as run_rebuilds, request as request_rebuild:
            response = self.bulk([self.row(self.first, ale=2),
                                  self.row(self.second, ale=3)])

            self.assertEqual(200, response.status_code, response.content)
            self.assertEqual(1, run_rebuilds.call_count)
            self.assertEqual(1, request_rebuild.call_count)

    def test_a_structural_save_leaves_the_mutation_counts_alone(self):
        """The expensive one, and the reason `only=` exists.

        `mutation_counts` pulls every ObservedMutation in the database into Python. A
        renumber provably changes no mutation count, so asking for it would be the one thing
        here that could make the feature feel broken in production.
        """
        run, request = self._patched()
        with run as run_rebuilds, request as request_rebuild:
            self.bulk([self.row(self.first, ale=2)])

            for call in (run_rebuilds.call_args, request_rebuild.call_args):
                asked_for = call.kwargs["only"]
                self.assertIn("sample_counts", asked_for)
                self.assertNotIn("mutation_counts", asked_for)
                # `overview` and `static_data` used to be named and then not-named here.
                # Neither is a rebuilder any longer -- the Overview's counts and the needle
                # plot are computed by the request that renders them -- so naming either
                # would be a name `get_rebuilders` silently skips, which reads as coverage.
                self.assertNotIn("overview", asked_for)
                self.assertNotIn("static_data", asked_for)

    def test_a_descriptive_save_rebuilds_nothing(self):
        run, request = self._patched()
        with run as run_rebuilds, request as request_rebuild:
            self.assertEqual(200, self.single(self.first, sample_name="renamed").status_code)

            run_rebuilds.assert_not_called()
            request_rebuild.assert_not_called()

    def test_toggling_the_population_flag_counts_as_structural(self):
        run, request = self._patched()
        with run as run_rebuilds, request as request_rebuild:
            self.single(self.first, is_population=1)
            self.assertEqual(1, run_rebuilds.call_count)
            self.assertEqual(1, request_rebuild.call_count)


class ExistingDuplicateNamesTestCase(SampleEditTestCase):
    """Nothing has ever stopped an import writing two samples with one name.

    A plain "is this name taken" rule would refuse every save on such an experiment,
    including the one fixing it, so only a name that actually changed is checked.
    """

    def setUp(self):
        super().setUp()
        self.first = self.make_sample(1, 1, 1, 1, name="dup")
        self.second = self.make_sample(1, 1, 2, 1, name="dup")

    def test_an_experiment_with_duplicate_names_can_still_be_saved(self):
        response = self.bulk([self.row(self.first, flask=4),
                              self.row(self.second)])

        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual(("1", 4, "1", 1), self.coordinate(self.first))

    def test_one_of_them_can_be_renamed_to_something_free(self):
        response = self.single(self.first, sample_name="distinct")

        self.assertEqual(200, response.status_code, response.content)
        self.first.refresh_from_db()
        self.assertEqual("distinct", self.first.sample_name)


class TimePointLabellingTestCase(SampleEditTestCase):
    """`Flask.flask_number` is the column; "time point" is what it means.

    It is the only ordinal in the schema placing a sample along an ALE -- fixation sorts
    by it and takes the last two to decide what has fixed -- so the edit pages call it
    what it is. The internal name must not surface, including in a refusal.
    """

    def setUp(self):
        super().setUp()
        self.sample = self.make_sample(1, 30000, 1, 1, name="first")

    def test_both_pages_label_it_time_point(self):
        for url in ("/ale/sample/%d/edit/" % self.sample.pk,
                    "/ale/experiment/%d/samples/" % self.experiment.id):
            with self.subTest(url=url):
                # {% comment %} blocks never render, so the internal name in the
                # template's own notes cannot make this pass by accident.
                html = self.client.get(url).content.decode()
                self.assertIn("Time point", html)
                self.assertNotIn(">Flask<", html)
                self.assertNotIn(">Flask</", html)

    def test_the_time_point_input_has_no_stepper(self):
        """type=number puts up/down arrows on a value that runs to five figures."""
        html = self.client.get("/ale/sample/%d/edit/" % self.sample.pk).content.decode()
        field = [line for line in html.splitlines() if 'id="se-flask"' in line][0]
        self.assertIn('type="text"', field)
        self.assertNotIn('type="number"', field)

    def test_a_refusal_does_not_leak_the_column_name(self):
        response = self.single(self.sample, flask="not a number")

        self.assertEqual(400, response.status_code)
        message = response.json()["error"]
        self.assertIn("time point", message)
        self.assertNotIn("flask", message.lower())

    def test_a_fractional_time_point_is_refused_clearly(self):
        """flask_number is an IntegerField, so a fraction cannot be stored. The plain
        input invites one, which makes the wording of this refusal load-bearing."""
        response = self.single(self.sample, flask="30000.5")

        self.assertEqual(400, response.status_code)
        self.assertIn("time point must be a whole number",
                      response.json()["error"])

    def test_a_large_time_point_saves(self):
        self.assertEqual(200, self.single(self.sample, flask=123456).status_code)
        self.assertEqual(("1", 123456, "1", 1), self.coordinate(self.sample))

"""igv tracks built from database rows.

The coordinate assertions are the point of this file. igv features are 0-based and
end-exclusive while GenomeDiff positions are 1-based inclusive, and an off-by-one here does
not fail -- it draws every mutation one base from where it is, beside the gene it is actually
in, entirely plausibly.
"""

from unittest import mock
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_import import breseq_folder
from aledb_import.tests import breseq_fixture
from aledb_sample import tracks
from aledb_sample.models import Mutation, MutationCall, Sample


class _Fixture(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="t", email="t@e.com", is_active=True)
        self.drop = tempfile.mkdtemp()
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.drop, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        breseq_fixture.write_sample(self.drop, "s1")
        breseq_folder.import_breseq_folders(
            self.drop, project_name="P", experiment_name="e", person="t")
        self.reseq = Sample.objects.get()
        self.experiment = self.reseq.experiment


class CoordinateTestCase(_Fixture):
    def test_a_feature_starts_one_before_the_stored_position(self):
        """1-based inclusive in, 0-based end-exclusive out."""
        mutation = Mutation.objects.filter(seq_id__isnull=False).first()
        features = tracks.mutation_features(self.experiment.id)
        feature = next(f for f in features if f["mutationId"] == mutation.id)

        self.assertEqual(mutation.position - 1, feature["start"])
        self.assertEqual(mutation.position, feature["end"])
        self.assertEqual(mutation.seq_id, feature["chr"])

    def test_a_span_keeps_its_length(self):
        """end - start is the number of bases covered, which is what end-exclusive buys."""
        Mutation.objects.filter(pk=Mutation.objects.first().pk).update(
            start_position=100, end_position=109)
        feature = next(f for f in tracks.mutation_features(self.experiment.id)
                       if f["start"] == 99)
        self.assertEqual(109, feature["end"])
        self.assertEqual(10, feature["end"] - feature["start"])

    def test_position_one_does_not_go_negative(self):
        Mutation.objects.filter(pk=Mutation.objects.first().pk).update(
            position=1, start_position=None, end_position=None)
        self.assertTrue(all(f["start"] >= 0
                            for f in tracks.mutation_features(self.experiment.id)))


class MutationTrackTestCase(_Fixture):
    def test_it_finds_a_mutation_owned_by_no_experiment(self):
        """The reason this reads through the calls rather than through
        `Mutation.experiment`. Two rows in the dev database were observed in an
        experiment while owned by none, so filtering on the column drew an empty Mutations
        track beside a populated per-sample one."""
        Mutation.objects.all().update(experiment=None)
        self.assertTrue(tracks.mutation_features(self.experiment.id))

    def test_both_feature_sets_describe_the_same_mutations(self):
        """Asked of the builders rather than of `database_tracks`, which no longer offers the
        per-sample track -- see `DRAW_SAMPLE_TRACK`. The relationship between the two is still
        the thing worth pinning, and `sample_features` is still what would be drawn."""
        starts = {f["start"] for f in tracks.mutation_features(self.experiment.id)}
        sample_starts = {f["start"] for f in tracks.sample_features(self.experiment.id)}
        self.assertTrue(sample_starts)
        self.assertTrue(sample_starts.issubset(starts))

    def test_only_the_mutations_track_is_offered(self):
        """The per-sample seg track is switched off, not broken: `sample_features` still
        builds features and `database_tracks` simply does not offer them. Both halves are
        asserted, so the day the switch flips this test says which half moved."""
        built = tracks.database_tracks(self.experiment.id)
        self.assertEqual(["Mutations"], [t["name"] for t in built])
        self.assertEqual(tracks.MUTATION_TRACK_ID, built[0]["id"])
        self.assertFalse(tracks.DRAW_SAMPLE_TRACK)
        self.assertTrue(tracks.sample_features(self.experiment.id))

    def test_flipping_the_switch_brings_it_back(self):
        """What `DRAW_SAMPLE_TRACK` is for. Left as a switch rather than deleted because the
        track works; what was decided is that it does not earn the space."""
        with mock.patch.object(tracks, "DRAW_SAMPLE_TRACK", True):
            built = tracks.database_tracks(self.experiment.id)
        self.assertEqual(["Mutations", "Mutations by sample"], [t["name"] for t in built])

    def test_the_colour_comes_from_the_functional_change_vocabulary(self):
        Mutation.objects.all().update(snp_type="nonsense")
        feature = tracks.mutation_features(self.experiment.id)[0]
        self.assertEqual(tracks.BUCKET_COLOURS["nonsense"], feature["color"])

    def test_an_unknown_snp_type_is_still_coloured(self):
        """`functional_change_bucket` answers UNANNOTATED for a token it does not know, and a
        page that raised on one would be worse than a page that called it unannotated."""
        Mutation.objects.all().update(snp_type="something_new")
        feature = tracks.mutation_features(self.experiment.id)[0]
        self.assertEqual(tracks.BUCKET_COLOURS[tracks.UNANNOTATED], feature["color"])

    def test_the_label_does_not_carry_a_whole_gene_list(self):
        Mutation.objects.all().update(gene=", ".join("gene%d" % i for i in range(500)))
        name = tracks.mutation_features(self.experiment.id)[0]["name"]
        self.assertIn("+499 more", name)
        self.assertLess(len(name), 100)

    def test_a_contig_filter_narrows_it(self):
        self.assertEqual([], tracks.mutation_features(self.experiment.id, contig="nope"))


class SampleTrackTestCase(_Fixture):
    def test_rows_are_named_by_the_shared_sample_label(self):
        """Not the sample's bare `description`, which is null for most samples -- pulling that through
        values_list collapsed every sample onto one row called "sample"."""
        features = tracks.sample_features(self.experiment.id)
        self.assertTrue(features)
        self.assertEqual({self.reseq.label},
                         {f["sample"] for f in features})

    def test_presence_is_uniform_and_frequency_rides_alongside(self):
        """The colour says only "called here". See SEG_PRESENT: seg autoscales to its own
        data range, so a frequency-derived colour would mean different things on different
        experiments' pages."""
        features = tracks.sample_features(self.experiment.id)
        self.assertEqual({tracks.SEG_PRESENT}, {f["value"] for f in features})
        self.assertTrue(all("frequency" in f for f in features))

    def test_absent_calls_are_not_drawn(self):
        MutationCall.objects.all().update(present=False)
        self.assertEqual([], tracks.sample_features(self.experiment.id))


class EmptyTestCase(_Fixture):
    def test_an_experiment_with_nothing_gets_no_tracks(self):
        """An empty track is worse than no track: igv draws its name and a blank lane, which
        reads as "no mutations here" rather than "nothing to show"."""
        MutationCall.objects.all().delete()
        self.assertEqual([], tracks.database_tracks(self.experiment.id))

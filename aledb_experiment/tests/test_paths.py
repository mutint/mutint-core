"""The shared join paths resolve against the real models.

`aledb_experiment.paths` replaced 39 hand-written copies of the traversal from a mutation or
a sample up to its experiment. Centralising them is only worth anything if the one remaining
copy is right, and a lookup string is the kind of thing that is wrong silently: Django raises
`FieldError` when it can resolve nothing, but a path that resolves to the *wrong* column
just returns no rows.

So these assert two different things. That each path **resolves** -- built against the real
model metadata, which a string-equality test would not catch -- and that the two paths
spelled `ale_id` at the end reach the two *different* columns that share that name.
"""

from django.core.exceptions import FieldError
from django.test import TestCase

from aledb_experiment import paths
from aledb_experiment.models import AleExperiment, AleId, Flask
from aledb_seq.models import ObservedMutation, ResequencingExperiment


class ResolutionTestCase(TestCase):
    """Every path is built into a real query. `.query` forces resolution without a database
    round trip, and raises FieldError on anything the ORM cannot follow."""

    def assert_resolves(self, model, path):
        try:
            str(model.objects.filter(**{path: 1}).query)
        except FieldError as error:
            self.fail("%s cannot follow %r: %s" % (model.__name__, path, error))

    def test_from_a_sample(self):
        for path in (paths.to_flask(), paths.to_ale(), paths.to_experiment(),
                     paths.to_experiment_id(), paths.to_ale_label(),
                     paths.to_flask_ordinal()):
            with self.subTest(path=path):
                self.assert_resolves(ResequencingExperiment, path)

    def test_from_an_observation(self):
        prefix = paths.FROM_OBSERVATION
        for path in (paths.to_flask(prefix), paths.to_ale(prefix),
                     paths.to_experiment(prefix), paths.to_experiment_id(prefix),
                     paths.to_ale_label(prefix), paths.to_flask_ordinal(prefix)):
            with self.subTest(path=path):
                self.assert_resolves(ObservedMutation, path)

    def test_a_field_can_be_appended(self):
        self.assert_resolves(ResequencingExperiment, paths.to_ale(field="strain"))
        self.assert_resolves(ObservedMutation,
                             paths.to_experiment(paths.FROM_OBSERVATION, "project_id"))


class TwoColumnsOneWordTestCase(TestCase):
    """`EXPERIMENT_PK` and `ALE_LABEL` were the same string and different columns.

    They are not any more: the experiment's primary key is `id`, like every other table's.
    Separating them into two constants first is what made that rename an edit of one line,
    and this class is the record of it -- `test_they_no_longer_share_a_spelling` failed the
    moment the rename landed, which is the only reason to have written it that way round.
    """

    def test_the_experiment_pk_is_the_experiments_primary_key(self):
        self.assertEqual(paths.EXPERIMENT_PK, AleExperiment._meta.pk.name)

    def test_the_experiment_has_no_ale_id_any_more(self):
        """The rename's whole point. `experiment.ale_id` was the pk; `flask.ale_id` is a row
        and `ale.ale_id` is a string, and all three read identically at the call site."""
        with self.assertRaises(Exception):
            AleExperiment._meta.get_field("ale_id")

    def test_the_ale_label_is_a_field_on_the_ale(self):
        field = AleId._meta.get_field(paths.ALE_LABEL)
        self.assertNotEqual(field, AleId._meta.pk,
                            "the ALE's label is not its primary key")

    def test_they_no_longer_share_a_spelling(self):
        self.assertNotEqual(paths.EXPERIMENT_PK, paths.ALE_LABEL)
        self.assertNotEqual(paths.to_experiment_id(), paths.to_ale_label())

    def test_the_flask_ordinal_is_the_one_fixation_sorts_by(self):
        self.assertEqual("IntegerField",
                         Flask._meta.get_field(paths.FLASK_ORDINAL).get_internal_type())


class JoinTestCase(TestCase):

    def test_a_prefix_may_carry_its_own_separator(self):
        """Callers wrote `"sequencing_experiment__"` when the old code concatenated, and one
        still does. Joined naively that is four underscores, which Django reports as
        `Unsupported lookup ''` -- a message that says nothing about the cause."""
        self.assertEqual(paths.to_experiment(paths.FROM_OBSERVATION),
                         paths.to_experiment("sequencing_experiment__"))

    def test_an_absent_prefix_is_skipped_rather_than_leading_the_path(self):
        self.assertFalse(paths.to_ale().startswith("_"))
        self.assertEqual(paths.TO_ALE, paths.to_ale(""))


class RootTestCase(TestCase):
    """A queryset that starts part-way along the chain gets the same definition.

    This is not hypothetical tidiness. `aledb_metadata.parser` was rooted part-way along
    and spelled its half of the chain by hand -- so it survived a sweep that searched for
    the chain's *first* segment, and was still filtering on a column that no longer
    existed. It failed loudly, but only because a test happened to cover that parser.

    There are two roots above the sample now rather than four: `Isolate` and
    `TechnicalReplicate` are folded into it, so the chain is three segments and a "root" of
    `sample` reaches the whole of it.
    """

    def test_each_root_drops_the_segments_before_it(self):
        self.assertEqual("flask__ale_id__ale_experiment", paths.chain("sample"))
        self.assertEqual("ale_id__ale_experiment", paths.chain("flask"))
        self.assertEqual("ale_experiment", paths.chain("ale"))

    def test_a_rooted_path_resolves(self):
        from aledb_experiment.models import AleId, Flask

        for model, root in ((Flask, "flask"), (AleId, "ale")):
            for path in (paths.to_experiment_id(root=root), paths.to_ale_label(root=root)):
                with self.subTest(model=model.__name__, path=path):
                    try:
                        str(model.objects.filter(**{path: 1}).query)
                    except FieldError as error:
                        self.fail("%s cannot follow %r: %s" % (model.__name__, path, error))

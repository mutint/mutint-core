"""The shared join paths resolve against the real models.

`mutint_experiment.paths` replaced 39 hand-written copies of the traversal from a mutation or
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

from mutint_experiment import paths
from mutint_experiment.models import Experiment, Population
from mutint_sample.models import MutationCall, Sample


class ResolutionTestCase(TestCase):
    """Every path is built into a real query. `.query` forces resolution without a database
    round trip, and raises FieldError on anything the ORM cannot follow."""

    def assert_resolves(self, model, path):
        try:
            str(model.objects.filter(**{path: 1}).query)
        except FieldError as error:
            self.fail("%s cannot follow %r: %s" % (model.__name__, path, error))

    def test_from_a_sample(self):
        for path in (paths.to_population(), paths.to_experiment(),
                     paths.to_experiment_id(), paths.to_population_label(),
                     paths.to_time_point_value()):
            with self.subTest(path=path):
                self.assert_resolves(Sample, path)

    def test_from_an_call(self):
        prefix = paths.FROM_CALL
        for path in (paths.to_population(prefix),
                     paths.to_experiment(prefix), paths.to_experiment_id(prefix),
                     paths.to_population_label(prefix), paths.to_time_point_value(prefix)):
            with self.subTest(path=path):
                self.assert_resolves(MutationCall, path)

    def test_a_field_can_be_appended(self):
        self.assert_resolves(Sample, paths.to_population(field="strain"))
        self.assert_resolves(MutationCall,
                             paths.to_experiment(paths.FROM_CALL, "project_id"))


class TwoColumnsOneWordTestCase(TestCase):
    """`EXPERIMENT_PK` and `ALE_LABEL` were the same string and different columns.

    They are not any more: the experiment's primary key is `id`, like every other table's.
    Separating them into two constants first is what made that rename an edit of one line,
    and this class is the record of it -- `test_they_no_longer_share_a_spelling` failed the
    moment the rename landed, which is the only reason to have written it that way round.
    """

    def test_the_experiment_pk_is_the_experiments_primary_key(self):
        self.assertEqual(paths.EXPERIMENT_PK, Experiment._meta.pk.name)

    def test_the_experiment_has_no_ale_id_any_more(self):
        """The rename's whole point. `experiment.ale_id` was the pk; `flask.ale_id` is a row
        and `ale.ale_id` is a string, and all three read identically at the call site."""
        with self.assertRaises(Exception):
            Experiment._meta.get_field("ale_id")

    def test_the_ale_label_is_a_field_on_the_ale(self):
        field = Population._meta.get_field(paths.POPULATION_LABEL)
        self.assertNotEqual(field, Population._meta.pk,
                            "the ALE's label is not its primary key")

    def test_they_no_longer_share_a_spelling(self):
        self.assertNotEqual(paths.EXPERIMENT_PK, paths.POPULATION_LABEL)
        self.assertNotEqual(paths.to_experiment_id(), paths.to_population_label())

    def test_the_time_point_is_a_column_on_the_sample_and_is_numeric(self):
        """It was `TimePoint.value`, an IntegerField one join away. Two things matter and
        the second is why this test moved rather than went: it has to be *reachable without
        a join*, which is what the removal was for, and it has to stay numeric, because
        mutint-fixation sorts by it to take a population's last two."""
        field = Sample._meta.get_field(paths.TIME_POINT_VALUE)
        self.assertEqual("FloatField", field.get_internal_type())
        self.assertEqual(paths.TIME_POINT_VALUE, paths.to_time_point_value(),
                         "a sample-rooted path to the time point is the bare column")


class JoinTestCase(TestCase):

    def test_a_prefix_may_carry_its_own_separator(self):
        """Callers wrote `"sample__"` when the old code concatenated, and one
        still does. Joined naively that is four underscores, which Django reports as
        `Unsupported lookup ''` -- a message that says nothing about the cause."""
        self.assertEqual(paths.to_experiment(paths.FROM_CALL),
                         paths.to_experiment("sample__"))

    def test_an_absent_prefix_is_skipped_rather_than_leading_the_path(self):
        self.assertFalse(paths.to_population().startswith("_"))
        self.assertEqual(paths.TO_POPULATION, paths.to_population(""))


class RootTestCase(TestCase):
    """A queryset that starts part-way along the chain gets the same definition.

    This is not hypothetical tidiness. The retired `mutint_metadata.parser` was rooted
    part-way along and spelled its half of the chain by hand -- so it survived a sweep that
    searched for the chain's *first* segment, and was still filtering on a column that no
    longer existed. It failed loudly, but only because a test happened to cover it.

    **There is one root above the sample now, where there were four.** `Isolate` and
    `TechnicalReplicate` folded into the sample and `TimePoint` became a column on it, so
    the chain is two segments and a "root" of `sample` reaches the whole of it.
    """

    def test_each_root_drops_the_segments_before_it(self):
        self.assertEqual("population__experiment", paths.chain("sample"))
        self.assertEqual("experiment", paths.chain("population"))

    def test_the_time_point_is_no_longer_a_root(self):
        """It is a column, so there is no queryset that could start at one."""
        self.assertNotIn("time_point", paths.ROOTS)
        self.assertNotIn("time_point", paths.DOWN_ROOTS)

    def test_a_rooted_path_resolves(self):
        from mutint_experiment.models import Population

        for model, root in ((Population, "population"),):
            for path in (paths.to_experiment_id(root=root), paths.to_population_label(root=root)):
                with self.subTest(model=model.__name__, path=path):
                    try:
                        str(model.objects.filter(**{path: 1}).query)
                    except FieldError as error:
                        self.fail("%s cannot follow %r: %s" % (model.__name__, path, error))

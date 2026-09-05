"""Where the shared mutation table's sample columns begin.

`table_template.js` placed the first sample column by arithmetic on REFSEQ_COLUMN_IN_MUT_TABLE
with offsets from a longer fixed set, and the result was silent: the first two sample columns
hidden by default, the first three unstyled, and -- on a one-sample experiment -- a DataTables
alert on every draw for a hidden column past the end of the table. The script reads one number
now, and this pins where it comes from.
"""

from django.test import SimpleTestCase

from mutint_common import constants
from mutint_common.context_processors import request_vocabulary


class FirstSampleColumnTestCase(SimpleTestCase):
    def test_it_is_the_length_of_the_fixed_header(self):
        self.assertEqual(len(constants.HTML_MUTATION_TABLE_HEADER),
                         constants.FIRST_SAMPLE_COLUMN_IN_MUT_TABLE)

    def test_it_sits_after_the_reference_column(self):
        self.assertGreater(constants.FIRST_SAMPLE_COLUMN_IN_MUT_TABLE,
                           constants.REFSEQ_COLUMN_IN_MUT_TABLE)

    def test_templates_get_it_without_a_view_passing_it(self):
        self.assertEqual(constants.FIRST_SAMPLE_COLUMN_IN_MUT_TABLE,
                         request_vocabulary(None)["FIRST_SAMPLE_COLUMN"])

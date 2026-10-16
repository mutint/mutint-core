"""A CSV for a spreadsheet starts with the UTF-8 byte-order mark."""

from django.test import SimpleTestCase

from mutint_common.spreadsheet import BOM, csv_response, for_spreadsheet


class SpreadsheetCsvTestCase(SimpleTestCase):

    def test_the_mark_goes_in_front_of_a_body(self):
        self.assertEqual("\ufeffa,b\n", for_spreadsheet("a,b\n"))
        self.assertEqual(b"\xef\xbb\xbf", BOM.encode("utf-8"))

    def test_a_response_is_an_attachment_that_starts_with_the_mark(self):
        response = csv_response("genes.csv")
        response.write("a,b\n")
        self.assertEqual("text/csv; charset=utf-8", response["Content-Type"])
        self.assertEqual('attachment; filename="genes.csv"', response["Content-Disposition"])
        self.assertEqual(b"\xef\xbb\xbfa,b\n", response.content)

import unittest

from mutint_import.util import sanitize_path
from mutint_import.util import get_ale_isolate_name_from_path

__author__ = 'Patrick Phaneuf'


class TestUtil(unittest.TestCase):

    def test_santize_path_not_previously_sanitized(self):

        path = "asdf/qwer"

        sanitized_path = sanitize_path(path)

        expected = "asdf/qwer/"

        self.assertEqual(sanitized_path, expected)

    def test_santize_path_already_sanitized(self):

        path = "asdf/qwer/"

        sanitized_path = sanitize_path(path)

        expected = "asdf/qwer/"

        self.assertEqual(sanitized_path, expected)

    def test_get_ale_isolate_name_from_path(self):

        expected_ale_isolate_name = "9-83-1"

        ale_isolate_path = "/data/breseq/glu/test_breseq/9-83-1"

        returned_ale_isolate_name = get_ale_isolate_name_from_path(ale_isolate_path)

        self.assertEqual(expected_ale_isolate_name, returned_ale_isolate_name)

        ale_isolate_path = "/data/breseq/glu/test_breseq/9-83-1/"

        returned_ale_isolate_name = get_ale_isolate_name_from_path(ale_isolate_path)

        self.assertEqual(expected_ale_isolate_name, returned_ale_isolate_name)
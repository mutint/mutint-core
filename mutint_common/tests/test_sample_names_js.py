"""The browser's copy of the naming rule, checked against the Python it mirrors.

`mutint_common/staticfiles/js/mutint_sample_names.js` transcribes
`mutint_import/sample_names.py` so a form can show a person the coordinate their name carries
while they type it. Two copies of one rule is a thing this codebase avoids, and what makes it
bearable here is that **the Python is authoritative**: every import parses the name again, so a
bug in the JS is a misleading preview and never a misplaced sample.

**What this test can and cannot do.** The suite has no JavaScript runtime -- adding one would
mean node or a browser in `tools.txt` for one file -- so this reads the case table out of the JS
source and asserts the *Python* parser answers what the JS file says it does. That catches the
two **specifications** drifting apart, which is the likely failure: somebody changes the Python
rule and the JS keeps claiming the old answers. It cannot catch the JS *implementation*
disagreeing with its own table. Running the JS over this table in a browser is a verification
step, not a suite fixture.

Reading a source file for an assertion is an established shape here -- `mutint_import/tests/
test_tasks.py` asserts an enqueue call site, `mutint_common/tests/test_templates.py` asserts
script order out of `base.html`.
"""

import json
import os
import re
import unittest

from mutint_import.sample_names import parse_sample_identity, sample_label

JS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "staticfiles", "js", "mutint_sample_names.js")


def js_parse_cases():
    """The `PARSE_CASES` array from the JS, as Python.

    Sliced out and read as JSON, which it is: a list of lists of strings, numbers and `null`.
    A parse failure here is the point -- if the table stops being readable, this test fails
    rather than silently checking nothing.
    """
    with open(JS_PATH) as handle:
        source = handle.read()
    match = re.search(r"PARSE_CASES:\s*(\[.*?\n\s*\])", source, re.S)
    if match is None:
        raise AssertionError("no PARSE_CASES table in %s" % JS_PATH)
    return json.loads(match.group(1))


class SampleNamesJsTestCase(unittest.TestCase):
    def test_the_file_is_there_and_carries_a_table(self):
        cases = js_parse_cases()

        self.assertGreater(len(cases), 10, "the case table has been gutted")

    def test_python_answers_what_the_js_table_claims(self):
        """Every row, both directions: the names that carry a coordinate and the ones that
        deliberately do not."""
        for case in js_parse_cases():
            name = case[0]
            with self.subTest(name=name):
                identity = parse_sample_identity(name)

                if len(case) == 2 and case[1] is None:
                    self.assertIsNone(
                        identity,
                        "the JS says %r carries no coordinate; Python reads one" % name)
                    continue

                self.assertIsNotNone(
                    identity, "the JS says %r carries a coordinate; Python reads none" % name)
                self.assertEqual(
                    [identity.population, identity.time_point,
                     sample_label(identity.name, identity.replicate)],
                    case[1:],
                    "the JS and Python disagree about %r" % name)

    def test_the_js_names_the_module_it_copies(self):
        """A reader who finds the copy has to be able to find the original, and has to be
        told which of the two is authoritative."""
        with open(JS_PATH) as handle:
            # The block comment's leading `*` is stripped and the whitespace collapsed, or
            # these assertions depend on where a sentence happened to wrap.
            source = " ".join(
                " ".join(line.lstrip(" *") for line in handle.read().splitlines()).split())

        self.assertIn("mutint_import/sample_names.py", source)
        self.assertIn("is the authority", source)
        self.assertIn("cannot misplace a sample", source)

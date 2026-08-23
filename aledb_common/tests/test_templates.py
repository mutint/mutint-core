"""Template hygiene checks that a rendering test would not necessarily catch.

`{# ... #}` is a *single-line* construct in Django. Spanning it across lines does
not comment anything out -- the text renders into the page verbatim, and nothing
raises. It shipped that way on the project list, where the reader saw a paragraph
of implementation notes above the table.
"""

import os
import re
import unittest

import aledb_common

CORE = os.path.dirname(os.path.dirname(os.path.abspath(aledb_common.__file__)))
COMMENT = re.compile(r"\{#(.*?)#\}", re.S)
SKIP = ("/env/", "/node_modules/", "/.claude/", "/staticfiles/")


def _templates():
    for root, _dirs, files in os.walk(CORE):
        if any(part in root + "/" for part in SKIP):
            continue
        for name in files:
            if name.endswith(".html"):
                yield os.path.join(root, name)


class TemplateCommentsTestCase(unittest.TestCase):

    def test_no_multiline_hash_comments(self):
        offenders = []
        for path in _templates():
            with open(path, errors="ignore") as handle:
                text = handle.read()
            for match in COMMENT.finditer(text):
                if "\n" in match.group(1):
                    line = text[:match.start()].count("\n") + 1
                    offenders.append("%s:%d" % (os.path.relpath(path, CORE), line))
        self.assertEqual(
            [], offenders,
            "{# #} does not span lines -- use {%% comment %%}. Offenders: %s"
            % ", ".join(offenders))

    def test_the_check_can_actually_see_templates(self):
        """Guards the walk itself: a bad root would make the test above vacuous."""
        found = list(_templates())
        self.assertGreater(len(found), 20)
        self.assertTrue(any(p.endswith("base.html") for p in found))

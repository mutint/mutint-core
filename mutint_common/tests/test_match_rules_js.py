"""The Filter tab's rules, run under node.

`mutint_match_rules.js` decides whether a row matches -- a substring of a column's text,
whole-word or case-sensitive on request, or a position range on a reference, either of
them inverted on request -- and both mutation tables apply it in the browser. Nothing on the server reads a rule, so this is
the only test of what one means. Skipped, saying why, where node is not installed.
"""

import json
import os
import shutil
import subprocess
import unittest

import mutint_common

NODE = shutil.which("node")
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(mutint_common.__file__)),
                      "staticfiles", "js", "mutint_match_rules.js")
SKIP_REASON = "node is not installed, so the Filter tab's rules cannot be run here"
COLUMNS = ["type", "seq_id", "position", "gene", "description"]

_PROGRAM = """
const api = require(process.argv[1]);
let input = "";
process.stdin.on("data", chunk => { input += chunk; });
process.stdin.on("end", () => {
    const job = JSON.parse(input);
    const out = job.calls.map(call => {
        const rules = api.normalize({rules: call.rules}, call.columns);
        if (call.normalize) { return {rules: rules}; }
        const test = api.compile(rules);
        return {matches: call.rows.map(row => test({
            text: key => row.cells[key] || "", seqId: row.seq_id || "", position: row.position
        }))};
    });
    process.stdout.write(JSON.stringify(out));
});
"""


def run(calls):
    completed = subprocess.run([NODE, "-e", _PROGRAM, SCRIPT], input=json.dumps({"calls": calls}),
                               capture_output=True, text=True, timeout=60, check=True)
    return json.loads(completed.stdout)


def row(gene, position=100, seq_id="REL606"):
    return {"cells": {"gene": gene, "type": "SNP"}, "position": position, "seq_id": seq_id}


@unittest.skipUnless(NODE, SKIP_REASON)
class MatchRulesTestCase(unittest.TestCase):

    def matches(self, rules, rows):
        return run([{"rules": rules, "columns": COLUMNS, "rows": rows}])[0]["matches"]

    def test_text_is_a_case_folded_substring_by_default(self):
        self.assertEqual([True, True, False],
                         self.matches([{"column": "gene", "text": "MUT"}],
                                      [row("mutT"), row("Smut"), row("thrA")]))

    def test_match_case_folds_nothing(self):
        self.assertEqual([False, True],
                         self.matches([{"column": "gene", "text": "MUT", "case": True}],
                                      [row("mutT"), row("MUT")]))

    def test_whole_word_stops_at_a_word_character(self):
        """`mut` is mut alone: not mutT, not mutS, not Smut. A slash, a comma or a space
        beside it is a boundary; a letter, a digit or an underscore is not."""
        self.assertEqual([True, False, False, False, True, True, False],
                         self.matches([{"column": "gene", "text": "mut", "whole": True}],
                                      [row("mut"), row("mutT"), row("mutS/mutL"), row("Smut"),
                                       row("yjjA/mut"), row("thrA, mut, thrB"), row("mut_1")]))

    def test_the_value_is_taken_literally(self):
        self.assertEqual([True, False],
                         self.matches([{"column": "gene", "text": "a.b"}], [row("a.b"), row("axb")]))
        self.assertEqual([True],
                         self.matches([{"column": "gene", "text": "thr(A)", "whole": True}],
                                      [row("thr(A)")]))

    def test_position_is_a_range_on_a_reference(self):
        rules = [{"column": "position", "seq_id": "REL606", "min": 10, "max": 20}]
        self.assertEqual([True, False, False, False],
                         self.matches(rules, [row("x", 15), row("x", 25), row("x", 5),
                                              row("x", 15, seq_id="pLA")]))

    def test_an_open_bound_and_any_reference(self):
        self.assertEqual([True, True, False],
                         self.matches([{"column": "position", "min": 10}],
                                      [row("x", 10), row("x", 10, seq_id="pLA"), row("x", 9)]))
        self.assertEqual([True, False],
                         self.matches([{"column": "position", "seq_id": "pLA"}],
                                      [row("x", 1, seq_id="pLA"), row("x", 1)]))

    def test_not_inverts_a_rule_of_either_kind(self):
        """Does not contain, and not between: the same rule, the other rows."""
        self.assertEqual([False, False, True],
                         self.matches([{"column": "gene", "text": "MUT", "not": True}],
                                      [row("mutT"), row("Smut"), row("thrA")]))
        self.assertEqual([False, True, True, True],
                         self.matches([{"column": "position", "seq_id": "REL606", "min": 10, "max": 20,
                                        "not": True}],
                                      [row("x", 15), row("x", 25), row("x", 5),
                                       row("x", 15, seq_id="pLA")]))
        self.assertEqual([False, True],
                         self.matches([{"column": "position", "seq_id": "pLA", "not": True}],
                                      [row("x", 1, seq_id="pLA"), row("x", 1)]))

    def test_rules_are_anded(self):
        rules = [{"column": "gene", "text": "thr"}, {"column": "position", "max": 20}]
        self.assertEqual([True, False, False],
                         self.matches(rules, [row("thrA", 10), row("thrA", 30), row("araA", 10)]))

    def test_an_empty_rule_keeps_everything(self):
        """Inverted too: "does not contain nothing" is a rule still being typed, not a rule
        that hides every row."""
        self.assertEqual([True], self.matches([{"column": "gene", "text": ""}], [row("x")]))
        self.assertEqual([True], self.matches([{"column": "gene", "text": "", "not": True}], [row("x")]))
        self.assertEqual([True], self.matches([{"column": "position"}], [row("x")]))
        self.assertEqual([True], self.matches([{"column": "position", "not": True}], [row("x")]))
        self.assertEqual([True], self.matches([], [row("x")]))

    def test_normalize_drops_what_this_page_cannot_apply(self):
        """A column the page does not offer, a shape that is not a rule, a bound that is not
        a number; and inverted bounds are put the right way round."""
        rules = [{"column": "freq", "text": "1"}, "junk", None,
                 {"column": "position", "min": "abc", "max": "500", "seq_id": 3},
                 {"column": "position", "min": 30, "max": 20},
                 {"column": "gene", "text": "thr", "whole": 1, "not": "yes"}]
        got = run([{"rules": rules, "columns": COLUMNS, "normalize": True}])[0]["rules"]
        self.assertEqual([{"column": "position", "seq_id": "", "min": None, "max": 500, "not": False},
                          {"column": "position", "seq_id": "", "min": 20, "max": 30, "not": False},
                          {"column": "gene", "text": "thr", "whole": True, "case": False, "not": True}],
                         got)
        self.assertEqual([], run([{"rules": "nope", "columns": COLUMNS, "normalize": True}])[0]["rules"])

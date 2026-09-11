/* What a sample's name says about where it belongs -- the browser's copy.
 *
 * **`mutint_import/sample_names.py` is the authority.** This is a transcription of it, so a
 * page can show a person the coordinate their name carries *while they are typing it*, and
 * the two can disagree only in what is shown, never in what happens: every import path parses
 * the name again, in Python, with the real rule. A bug here is a misleading preview. It cannot
 * misplace a sample.
 *
 * Keep the two in step. `PARSE_CASES` below is the same table
 * `mutint_import/tests/test_sample_names.py` works from, and
 * `mutint_common/tests/test_sample_names_js.py` reads it out of this file and asserts the
 * Python parser agrees with every expectation written here.
 *
 * Two shapes, and a name matching neither carries no coordinate at all -- the import
 * auto-numbers it onto the `Unspecified` population with no time point.
 *
 *   A-F-I-R      `3-30000-1-1`         four dash-separated integers, strictly
 *   triple       `Ara-2_500gen_763A`   exactly three underscore-separated fields
 *
 * **Two things Python's `int()` accepts and this does not**, both found by running the two
 * over the same names: digits that are not ASCII (`１-２-３-４`) and Python's own `_` digit
 * separators (`3-3_0-1-1` is 30 there). Both make this answer "no coordinate" for a name the
 * import would place -- the preview under-promises, which is the safe direction, and neither
 * is reachable from a form that composes the name itself. Matching `int()` exactly would mean
 * a Unicode-aware digit class and a separator rule, for names nobody writes.
 */
(function () {
    "use strict";

    /* The number in a time-point field, with the unit on either side: `500gen`, `1500`,
     * `30000cd`, `day7`, `t12`, `h24`. A leading unit used to mean the name did not place at
     * all; see `_LEADING_NUMBER` in sample_names.py for why that changed. The unit is
     * discarded either way -- a time point is one unit-less number. */
    var LEADING_NUMBER = /^[A-Za-z]*(\d+)/;

    /* The strict four-integer form. Strict on purpose: a lenient reader turns a name it
     * cannot read into 1-1-1-1 and lands a whole drop on one sample. */
    function parseAfir(name) {
        var fields = name.split("-");
        if (fields.length < 4) { return null; }
        var numbers = [];
        for (var i = 0; i < 4; i += 1) {
            /* `parseInt` is not the Python `int`: it reads a leading number and shrugs at the
             * rest, so `1a` would pass. The whole field has to be a number, and a sign is
             * part of one -- Python's `int("+1")` is 1, and a field of `+1` used to read here
             * as no coordinate at all. */
            if (!/^[+-]?\d+$/.test(fields[i])) { return null; }
            numbers.push(parseInt(fields[i], 10));
        }
        return {
            population: String(numbers[0]),
            timePoint: numbers[1],
            name: String(numbers[2]),
            replicate: numbers[3],
            shape: "afir"
        };
    }

    /* `Ara-2_500gen_763A`. Exactly three fields: with two there is no telling whether the
     * population or the sample was omitted, and four would have to guess which is extra. */
    function parseTriple(name) {
        var fields = name.split("_");
        if (fields.length !== 3) { return null; }
        var population = fields[0].trim();
        var timePoint = fields[1].trim();
        var sample = fields[2].trim();
        if (!population || !sample) { return null; }
        var match = LEADING_NUMBER.exec(timePoint);
        if (match === null) { return null; }
        /* `replicate` is null, not 1: this shape has no such field, and `label` uses the
         * difference to decide whether the name gets a suffix. */
        return {
            population: population,
            timePoint: parseInt(match[1], 10),
            name: sample,
            replicate: null,
            shape: "triple"
        };
    }

    /* What the sample is called within its time point: `1-2`, `763A`, `1-1`. The suffix is
     * kept even when the replicate is 1 -- the label is exactly what the name spelled. */
    function label(name, replicate) {
        return replicate === null || replicate === undefined
            ? String(name)
            : String(name) + "-" + String(replicate);
    }

    window.mutintSampleName = {
        /* The coordinate a name carries, or null. A-F-I-R is checked first, so a name
         * satisfying both shapes reads as A-F-I-R. */
        parse: function (name) {
            var identity = parseAfir(name || "") || parseTriple(name || "");
            if (identity === null) { return null; }
            return {
                population: identity.population,
                timePoint: identity.timePoint,
                sample: label(identity.name, identity.replicate),
                shape: identity.shape
            };
        },

        /* The three parts as one name. **Always underscores**: one rule, and the coordinate
         * is the same either way -- only the label differs. Empty when any part is missing,
         * because a name carries all three or none of them. */
        compose: function (population, timePoint, sample) {
            var parts = [population, timePoint, sample].map(function (part) {
                return String(part === null || part === undefined ? "" : part).trim();
            });
            if (parts.some(function (part) { return !part; })) { return ""; }
            return parts.join("_");
        },

        /* The names `test_sample_names.py` works from, with what the Python rule answers for
         * each: [name, population, timePoint, sample] or [name, null] for one carrying no
         * coordinate. Read by `test_sample_names_js.py`. */
        PARSE_CASES: [
            ["3-30000-1-1", "3", 30000, "1-1"],
            ["3-30000-1-2", "3", 30000, "1-2"],
            ["3-30000-1-1-repeat", "3", 30000, "1-1"],
            ["Ara-2_500gen_763A", "Ara-2", 500, "763A"],
            ["Ara-2_500_763A", "Ara-2", 500, "763A"],
            ["Ara-2_50000gen_763A", "Ara-2", 50000, "763A"],
            ["Ara-2_30000cd_763A", "Ara-2", 30000, "763A"],
            ["Ara+1_500gen_763A", "Ara+1", 500, "763A"],
            ["1-2-3-4-x_500gen_y", "1", 2, "3-4"],
            ["3-30000-1-+1", "3", 30000, "1-1"],
            ["3-30000-1-01", "3", 30000, "1-1"],
            ["0-0-0-0", "0", 0, "0-0"],
            [" Ara-2 _ 500 _ 763A ", "Ara-2", 500, "763A"],
            ["Ara-2_1e3_763A", "Ara-2", 1, "763A"],
            ["x_5_y", "x", 5, "y"],
            ["1-2-3-x_500gen_y", "1-2-3-x", 500, "y"],
            ["9-83-1", null],
            ["3-30000-1-A", null],
            ["Ara-2_t0_763A", "Ara-2", 0, "763A"],
            ["pop3_day7_clone2", "pop3", 7, "clone2"],
            ["Ara-2_gen_763A", null],
            ["Ara-2_500gen", null],
            ["Ara-2_500gen_763A_rerun", null],
            ["_500gen_763A", null],
            ["Ara-2_500gen_", null],
            ["Ara-2__763A", null],
            ["everything", null],
            ["my_clone", null],
            ["a-b-c-d", null],
            ["-1-2-3-4", null],
            ["1-2-3-4.5", null],
            ["3--1-2", null],
            ["x__y", null],
            ["", null]
        ]
    };
}());

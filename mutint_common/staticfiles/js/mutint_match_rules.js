/* The Filter tab's rules: what a rule is, whether a row matches, and the controls that edit
 * them. One module for the two tables that offer the tab -- the mutation matrix
 * (mutation_matrix.js) and the per-sample Mutations page (breseq_match.js) -- so a rule
 * typed on one means exactly the same on the other, which is what lets them share the
 * remembered `mutation_matrix.filter.<experiment>`.
 *
 * A rule names a column and either text -- matched as a substring of the cell's plain
 * text, case-folded unless `case`, and only where no word character (a letter, a digit or
 * an underscore) sits at either end of the match when `whole`, so `mut` finds mut, and
 * mut/yjjA, and not mutT -- or, for Position, a reference sequence and two bounds, either
 * open. Several rules are AND-ed. The stored shape:
 *
 *   {"rules": [{"column": "gene", "text": "mut", "whole": false, "case": false},
 *              {"column": "position", "seq_id": "REL606", "min": 1, "max": 500000}]}
 *
 * `normalize` is the one reader of that shape, and it is forgiving: a rule naming a column
 * the page does not offer is dropped, a bound that is not a number is open. Matching is a
 * function over an adapter rather than over a row, because the two tables hold their rows
 * differently -- the matrix as objects of HTML strings, the per-sample page as `<tr>`s --
 * and each supplies `{text(key), seqId, position}` for its own.
 *
 * `mount` draws one clone of the pane's <template> per rule, and the page hands it the
 * store: this module saves nothing itself. Exported for node too, so a test can run the
 * rules outside a browser (the same guard compare_sets.js uses).
 */
(function () {
    "use strict";

    var WORD = "[\\p{L}\\p{N}_]";
    var POSITION = "position";

    function escapeRegExp(text) {
        // Only the syntax characters: under the `u` flag, escaping anything else is an error.
        return text.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
    }

    function finite(value) {
        if (typeof value === "string") { value = value.trim() === "" ? NaN : Number(value); }
        return typeof value === "number" && isFinite(value) ? value : null;
    }

    /* `stored` as the store returned it, into the list of rules this page can apply. */
    function normalize(stored, columnKeys) {
        var offered = {};
        (columnKeys || []).forEach(function (key) { offered[key] = true; });
        var list = stored && Array.isArray(stored.rules) ? stored.rules : [];
        var rules = [];
        list.forEach(function (rule) {
            if (!rule || typeof rule !== "object" || !offered[rule.column]) { return; }
            if (rule.column === POSITION) {
                var min = finite(rule.min), max = finite(rule.max);
                if (min !== null && max !== null && min > max) { var t = min; min = max; max = t; }
                rules.push({ column: POSITION, seq_id: typeof rule.seq_id === "string" ? rule.seq_id : "",
                             min: min, max: max });
            } else {
                rules.push({ column: rule.column, text: typeof rule.text === "string" ? rule.text : "",
                             whole: !!rule.whole, "case": !!rule["case"] });
            }
        });
        return rules;
    }

    /* Whether a rule narrows anything: an empty text box, or a position rule with no
       reference and no bound, is a rule still being typed. */
    function effective(rule) {
        if (rule.column === POSITION) { return !!rule.seq_id || rule.min !== null || rule.max !== null; }
        return rule.text !== "";
    }

    function pattern(rule) {
        var body = escapeRegExp(rule.text);
        if (rule.whole) { body = "(?<!" + WORD + ")" + body + "(?!" + WORD + ")"; }
        return new RegExp(body, rule["case"] ? "u" : "iu");
    }

    /* `fn(row)` over `{text(key) -> string, seqId, position}`: true when every rule holds. */
    function compile(rules) {
        var tests = rules.filter(effective).map(function (rule) {
            if (rule.column === POSITION) {
                return function (row) {
                    if (rule.seq_id && row.seqId !== rule.seq_id) { return false; }
                    var at = row.position;
                    if (typeof at !== "number" || isNaN(at)) { return rule.min === null && rule.max === null; }
                    if (rule.min !== null && at < rule.min) { return false; }
                    if (rule.max !== null && at > rule.max) { return false; }
                    return true;
                };
            }
            var re = pattern(rule);
            return function (row) { return re.test(row.text(rule.column) || ""); };
        });
        return function (row) {
            for (var i = 0; i < tests.length; i++) { if (!tests[i](row)) { return false; } }
            return true;
        };
    }

    /* The plain text of a rendered cell: its text nodes, less a button's -- a collapsed
       gene list carries a Show button whose word is not the cell's. */
    function plainText(node) {
        var out = "";
        (function walk(n) {
            if (n.nodeType === 3) { out += n.nodeValue; return; }
            if (n.nodeType !== 1 || n.tagName === "BUTTON") { return; }
            for (var c = n.firstChild; c; c = c.nextSibling) { walk(c); }
        }(node));
        return out.replace(/\s+/g, " ").trim();
    }

    function quote(text) { return "“" + text + "”"; }
    function number(value) { return value.toLocaleString("en-US"); }

    /* One sentence saying what the rules keep, or that they keep everything. */
    function describe(rules, titles) {
        titles = titles || {};
        var parts = rules.filter(effective).map(function (rule) {
            var title = titles[rule.column] || rule.column;
            if (rule.column === POSITION) {
                var where = "whose " + title + (rule.seq_id ? " on " + rule.seq_id : "");
                if (rule.min !== null && rule.max !== null) { return where + " is " + number(rule.min) + "–" + number(rule.max); }
                if (rule.min !== null) { return where + " is at least " + number(rule.min); }
                if (rule.max !== null) { return where + " is at most " + number(rule.max); }
                return where.replace("whose " + title + " on ", "on ");
            }
            return "whose " + title + " contains " + (rule.whole ? "the word " : "") + quote(rule.text) +
                   (rule["case"] ? " (matching case)" : "");
        });
        if (!parts.length) { return "Every mutation is shown."; }
        return "Showing mutations " + parts.join(" and ") + ".";
    }

    /* The columns the pane's template offers: `[{key, title, kind}]`. */
    function columnsOf(box) {
        var template = box.querySelector('template[data-role="match-rule"]');
        var select = template && template.content.querySelector('[data-role="match-column"]');
        if (!select) { return []; }
        return Array.prototype.map.call(select.querySelectorAll("option"), function (option) {
            return { key: option.value, title: option.textContent, kind: option.getAttribute("data-kind") || "text" };
        });
    }

    /* Draw `rules` in `box` and call `onChange(rules)` -- normalized -- whenever the reader
       changes one: on a text box's change or Enter, a menu or a checkbox, and at once on
       add, remove and Clear. Returns `{rules(), set(rules)}`. */
    function mount(box, options) {
        var template = box.querySelector('template[data-role="match-rule"]');
        var list = box.querySelector('[data-role="match-rules"]');
        var columns = columnsOf(box);
        var keys = columns.map(function (c) { return c.key; });
        var titles = {};
        columns.forEach(function (c) { titles[c.key] = c.title; });
        var summary = box.parentNode.querySelector('[data-role="match-summary"]') ||
                      document.querySelector('[data-role="match-summary"]');

        function read() {
            var rules = [];
            Array.prototype.forEach.call(list.querySelectorAll(".mutation-matrix-match-rule"), function (row) {
                var column = row.querySelector('[data-role="match-column"]').value;
                if (column === POSITION) {
                    rules.push({ column: column,
                                 seq_id: row.querySelector('[data-role="match-seq-id"]').value,
                                 min: row.querySelector('[data-role="match-min"]').value,
                                 max: row.querySelector('[data-role="match-max"]').value });
                } else {
                    rules.push({ column: column,
                                 text: row.querySelector('[data-role="match-text"]').value,
                                 whole: row.querySelector('[data-role="match-whole"]').checked,
                                 "case": row.querySelector('[data-role="match-case"]').checked });
                }
            });
            return normalize({ rules: rules }, keys);
        }

        function announce() {
            var rules = read();
            if (summary) { summary.textContent = describe(rules, titles); }
            if (options.onChange) { options.onChange(rules); }
        }

        function showFields(row) {
            var select = row.querySelector('[data-role="match-column"]');
            var isPosition = select.value === POSITION;
            row.querySelector('[data-role="match-text-field"]').hidden = isPosition;
            row.querySelector('[data-role="match-position-fields"]').hidden = !isPosition;
            var seq = row.querySelector('[data-role="match-seq-id"]');
            var chosen = seq.options[seq.selectedIndex];
            row.querySelector('[data-role="match-max"]').placeholder =
                chosen && chosen.getAttribute("data-length") ? number(parseInt(chosen.getAttribute("data-length"), 10)) : "";
        }

        function addRow(rule) {
            var row = template.content.firstElementChild.cloneNode(true);
            var select = row.querySelector('[data-role="match-column"]');
            if (rule) {
                select.value = rule.column;
                if (rule.column === POSITION) {
                    row.querySelector('[data-role="match-seq-id"]').value = rule.seq_id;
                    row.querySelector('[data-role="match-min"]').value = rule.min === null ? "" : rule.min;
                    row.querySelector('[data-role="match-max"]').value = rule.max === null ? "" : rule.max;
                } else {
                    row.querySelector('[data-role="match-text"]').value = rule.text;
                    row.querySelector('[data-role="match-whole"]').checked = rule.whole;
                    row.querySelector('[data-role="match-case"]').checked = rule["case"];
                }
            }
            showFields(row);
            select.addEventListener("change", function () { showFields(row); announce(); });
            row.querySelector('[data-role="match-seq-id"]').addEventListener("change", function () { showFields(row); announce(); });
            Array.prototype.forEach.call(row.querySelectorAll('input[type="text"]'), function (input) {
                input.addEventListener("change", announce);
                input.addEventListener("keydown", function (event) {
                    if (event.key === "Enter") { event.preventDefault(); announce(); }
                });
            });
            Array.prototype.forEach.call(row.querySelectorAll('input[type="checkbox"]'), function (input) {
                input.addEventListener("change", announce);
            });
            row.querySelector('[data-role="match-remove"]').addEventListener("click", function () {
                row.remove();
                if (!list.children.length) { addRow(null); }
                announce();
            });
            list.appendChild(row);
            return row;
        }

        function set(rules) {
            list.textContent = "";
            rules.forEach(addRow);
            if (!rules.length) { addRow(null); }
            if (summary) { summary.textContent = describe(read(), titles); }
        }

        box.querySelector('[data-role="match-add"]').addEventListener("click", function () {
            addRow(null).querySelector('[data-role="match-text"]').focus();
        });
        box.querySelector('[data-role="match-clear"]').addEventListener("click", function () {
            set([]);
            announce();
        });
        set(normalize({ rules: options.rules || [] }, keys));
        return { rules: read, set: set, columns: columns, titles: titles };
    }

    var api = { normalize: normalize, compile: compile, describe: describe, mount: mount,
                columnsOf: columnsOf, plainText: plainText, effective: effective };
    if (typeof window !== "undefined") { window.mutintMatchRules = api; }
    if (typeof module !== "undefined" && module.exports) { module.exports = api; }
}());

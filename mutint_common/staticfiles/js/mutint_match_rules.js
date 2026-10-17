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
 * open. `not` inverts a rule of either kind: does not contain, or not between. Each rule
 * after the first says how it joins the rules above it, `join`: "and" binds tighter than
 * "or", so the rules form or-separated groups, each the and of its rules, and a row shows
 * when any group holds in full. The first rule's `join` is ignored. The stored shape:
 *
 *   {"rules": [{"column": "gene", "text": "mut", "whole": false, "case": false, "not": false,
 *               "join": "or"},
 *              {"column": "position", "seq_id": "REL606", "min": 1, "max": 500000, "not": true,
 *               "join": "and"}]}
 *
 * `normalize` is the one reader of that shape, and it is forgiving: a rule naming a column
 * the page does not offer is dropped, a bound that is not a number is open, a `join` that
 * is not "and" is "or". Matching is a
 * function over an adapter rather than over a row, because the two tables hold their rows
 * differently -- the matrix as objects of HTML strings, the per-sample page as `<tr>`s --
 * and each supplies `{text(key), seqId, position}` for its own.
 *
 * `mount` draws one clone of the pane's <template> per rule in force, locked, and one more
 * being typed: a rule takes effect when its "+ Add filter" button is pressed (or Enter in
 * one of its boxes), never as it is typed, and a rule in force is only ever removed. The
 * first row is led by "Show mutations where:" and every later one by its and/or menu, so
 * the page says how the rules combine without a sentence. The page hands it the store: this module
 * saves nothing itself. Exported for node too, so a
 * test can run the rules outside a browser (the same guard compare_sets.js uses).
 */
(function () {
    "use strict";

    var WORD = "[\\p{L}\\p{N}_]";
    var POSITION = "position";
    var ROW = ".mutation-matrix-match-rule";
    var ADD_LABEL = "+ Add filter";
    var REMOVE_LABEL = "– Remove filter";
    var FIRST_LEAD = "Show mutations where:";

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
            var join = rule.join === "and" ? "and" : "or";
            if (rule.column === POSITION) {
                var min = finite(rule.min), max = finite(rule.max);
                if (min !== null && max !== null && min > max) { var t = min; min = max; max = t; }
                rules.push({ column: POSITION, seq_id: typeof rule.seq_id === "string" ? rule.seq_id : "",
                             min: min, max: max, not: !!rule.not, join: join });
            } else {
                rules.push({ column: rule.column, text: typeof rule.text === "string" ? rule.text : "",
                             whole: !!rule.whole, "case": !!rule["case"], not: !!rule.not, join: join });
            }
        });
        return rules;
    }

    /* Whether a rule says anything: an empty text box, or a position rule with no
       reference and no bound, is a rule still being typed -- inverted or not. */
    function effective(rule) {
        if (rule.column === POSITION) { return !!rule.seq_id || rule.min !== null || rule.max !== null; }
        return rule.text !== "";
    }

    function pattern(rule) {
        var body = escapeRegExp(rule.text);
        if (rule.whole) { body = "(?<!" + WORD + ")" + body + "(?!" + WORD + ")"; }
        return new RegExp(body, rule["case"] ? "u" : "iu");
    }

    function positive(rule) {
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
    }

    function test(rule) {
        var holds = positive(rule);
        return rule.not ? function (row) { return !holds(row); } : holds;
    }

    /* `fn(row)` over `{text(key) -> string, seqId, position}`: true when every rule of any
       group holds, a group being a first or "or" rule and the "and" rules after it; a rule
       that says nothing leaves its group, and a row is true when no group is left. */
    function compile(rules) {
        var groups = [];
        rules.forEach(function (rule, i) {
            if (i === 0 || rule.join !== "and") { groups.push([]); }
            if (effective(rule)) { groups[groups.length - 1].push(test(rule)); }
        });
        groups = groups.filter(function (group) { return group.length > 0; });
        if (groups.length === 0) { return function () { return true; }; }
        return function (row) {
            return groups.some(function (group) {
                return group.every(function (holds) { return holds(row); });
            });
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

    function number(value) { return value.toLocaleString("en-US"); }

    /* The columns the pane's template offers: `[{key, title, kind}]`. */
    function columnsOf(box) {
        var template = box.querySelector('template[data-role="match-rule"]');
        var select = template && template.content.querySelector('[data-role="match-column"]');
        if (!select) { return []; }
        return Array.prototype.map.call(select.querySelectorAll("option"), function (option) {
            return { key: option.value, title: option.textContent, kind: option.getAttribute("data-kind") || "text" };
        });
    }

    /* Draw `rules` in `box`, each locked with a Remove button, and one blank row under
       them being typed; call `onChange(rules)` -- normalized, the rules in force -- when a
       typed row is added, a rule removed, or every rule removed. Returns `{rules(), set(rules)}`. */
    function mount(box, options) {
        var template = box.querySelector('template[data-role="match-rule"]');
        var list = box.querySelector('[data-role="match-rules"]');
        var clear = box.querySelector('[data-role="match-clear"]');
        var columns = columnsOf(box);
        var keys = columns.map(function (c) { return c.key; });

        function field(row, role) {
            // The two field sets each carry a `match-not` menu; take the one that shows.
            var holder = row.querySelector('[data-role="match-column"]').value === POSITION ?
                '[data-role="match-position-fields"]' : '[data-role="match-text-field"]';
            return row.querySelector(holder + ' [data-role="' + role + '"]');
        }

        function readRow(row) {
            var column = row.querySelector('[data-role="match-column"]').value;
            var not = field(row, "match-not").value === "1";
            var join = row.querySelector('[data-role="match-join"]').value;
            if (column === POSITION) {
                return { column: column, not: not, join: join,
                         seq_id: row.querySelector('[data-role="match-seq-id"]').value,
                         min: row.querySelector('[data-role="match-min"]').value,
                         max: row.querySelector('[data-role="match-max"]').value };
            }
            return { column: column, not: not, join: join,
                     text: row.querySelector('[data-role="match-text"]').value,
                     whole: row.querySelector('[data-role="match-whole"]').checked,
                     "case": row.querySelector('[data-role="match-case"]').checked };
        }

        function inForce() { return list.querySelectorAll(ROW + ".committed"); }

        /* The rules in force: the locked rows, never the one being typed. */
        function read() {
            return normalize({ rules: Array.prototype.map.call(inForce(), readRow) }, keys);
        }

        /* The first row is led by "Show mutations where:" and every later one, the row
           being typed included, by its and/or menu. */
        function relabel() {
            Array.prototype.forEach.call(list.querySelectorAll(ROW), function (row, i) {
                var text = row.querySelector('[data-role="match-lead-text"]');
                text.textContent = i === 0 ? FIRST_LEAD : "";
                text.hidden = i !== 0;
                row.querySelector('[data-role="match-join"]').hidden = i === 0;
            });
        }

        function announce() {
            clear.hidden = inForce().length < 2;
            relabel();
            if (options.onChange) { options.onChange(read()); }
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

        function lock(row) {
            row.classList.add("committed");
            Array.prototype.forEach.call(row.querySelectorAll("select, input"), function (control) {
                control.disabled = true;
            });
            var button = row.querySelector('[data-role="match-add"]');
            button.setAttribute("data-role", "match-remove");
            button.classList.remove("btn-primary");
            button.classList.add("btn-danger");
            button.textContent = REMOVE_LABEL;
        }

        function focusDraft(row) {
            (row.querySelector('[data-role="match-column"]').value === POSITION ?
                row.querySelector('[data-role="match-min"]') : row.querySelector('[data-role="match-text"]')).focus();
        }

        /* The typed row becomes a rule in force, if it says anything, and a blank row
           follows it. */
        function commit(row) {
            var rule = normalize({ rules: [readRow(row)] }, keys)[0];
            if (!rule || !effective(rule)) { focusDraft(row); return; }
            lock(row);
            focusDraft(addRow(null, false));
            announce();
        }

        function addRow(rule, committed) {
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
                field(row, "match-not").value = rule.not ? "1" : "";
                row.querySelector('[data-role="match-join"]').value = rule.join;
            }
            showFields(row);
            select.addEventListener("change", function () { showFields(row); });
            row.querySelector('[data-role="match-seq-id"]').addEventListener("change", function () { showFields(row); });
            Array.prototype.forEach.call(row.querySelectorAll('input[type="text"]'), function (input) {
                input.addEventListener("keydown", function (event) {
                    if (event.key === "Enter") { event.preventDefault(); commit(row); }
                });
            });
            row.querySelector('[data-role="match-add"]').addEventListener("click", function () {
                if (row.classList.contains("committed")) {
                    row.remove();
                    announce();
                } else {
                    commit(row);
                }
            });
            list.appendChild(row);
            if (committed) { lock(row); }
            return row;
        }

        function set(rules) {
            list.textContent = "";
            rules.forEach(function (rule) { addRow(rule, true); });
            addRow(null, false);
            clear.hidden = inForce().length < 2;
            relabel();
        }

        clear.addEventListener("click", function () {
            set([]);
            announce();
        });
        set(normalize({ rules: options.rules || [] }, keys));
        return { rules: read, set: set, columns: columns };
    }

    var api = { normalize: normalize, compile: compile, mount: mount,
                columnsOf: columnsOf, plainText: plainText, effective: effective };
    if (typeof window !== "undefined") { window.mutintMatchRules = api; }
    if (typeof module !== "undefined" && module.exports) { module.exports = api; }
}());

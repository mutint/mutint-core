/* The Filter tab on the per-sample Mutations page: rules over the columns, OR-ed.
 *
 * The same tab the mutation matrix has, remembering the same choice --
 * `mutation_matrix.filter.<experiment>` -- so a rule typed on Compare is in force here and
 * back. What a rule is, and whether a row matches it, is mutint_match_rules.js; this file
 * is only the page's side: read each `<tr>` once into the adapter the rules test, and hand
 * that test to breseq_references.js, which owns hiding rows and re-striping what is left
 * (two scripts each setting `hidden` would fight over the stripes and the empty row). So
 * this loads after it, and does nothing where its container is absent.
 *
 * A cell's text is its text nodes less any button's -- a collapsed gene list keeps every
 * name in the DOM, hidden, behind a Show button -- and is not case-folded here: the rule
 * decides. Its own file for breseq_references.js's reason: breseq_table.js loads on pages
 * that have no tab strip.
 */
(function () {
    "use strict";

    var KEY_PREFIX = "mutation_matrix.filter.";

    function init(container) {
        var box = container.querySelector('[data-role="match"]');
        var references = document.querySelector("[data-breseq-references]");
        var table = document.querySelector("table.breseq-table");
        if (!box || !table || !references || !references.breseqReferences) { return; }
        var rules = window.mutintMatchRules;
        var experimentId = container.getAttribute("data-experiment-id");
        var key = experimentId ? KEY_PREFIX + experimentId : null;
        var prefs = window.mutintPreferences({
            authenticated: container.getAttribute("data-authenticated") === "1",
            url: container.getAttribute("data-preferences-url"),
            embedded: window.mutintPreferences.embedded("breseq-references-prefs")
        });
        var keys = rules.columnsOf(box).map(function (c) { return c.key; });
        var test = rules.compile(rules.normalize(key ? prefs.get(key, null) : null, keys));

        // Each row read once, lazily per column.
        function view(tr) {
            if (!tr._match) {
                var plain = {};
                var position = parseInt(tr.getAttribute("data-position"), 10);
                tr._match = {
                    seqId: tr.getAttribute("data-seq-id") || "",
                    position: isNaN(position) ? null : position,
                    text: function (column) {
                        if (!Object.prototype.hasOwnProperty.call(plain, column)) {
                            var cell = tr.querySelector('[data-column="' + column + '"]');
                            plain[column] = cell ? rules.plainText(cell) : "";
                        }
                        return plain[column];
                    }
                };
            }
            return tr._match;
        }

        references.breseqReferences.addPredicate(function (tr) { return test(view(tr)); });

        var ui = rules.mount(box, {
            rules: (key && prefs.get(key, null) || {}).rules || [],
            onChange: function (changed) {
                test = rules.compile(changed);
                references.breseqReferences.apply();
                if (key) { prefs.set(key, { rules: changed }); }
            }
        });
        container.breseqMatch = { rules: ui, test: function (tr) { return test(view(tr)); } };
    }

    document.addEventListener("DOMContentLoaded", function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-breseq-match]"), init);
    });
}());

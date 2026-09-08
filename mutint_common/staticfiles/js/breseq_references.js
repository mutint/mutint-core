/* The References menu on the per-sample Mutations page: which reference sequences the rows
 * may be on.
 *
 * The same menu the mutation matrix has (mutation_matrix.js), remembering the same choice --
 * `mutation_matrix.references.<experiment>`, the hidden set -- so hiding a plasmid on Compare
 * hides it here and back. The two scripts differ only in what they filter: the matrix is a
 * DataTable and filters through its search hook; this table is server-rendered, so each row
 * carries its reference as `data-seq-id` and the menu sets `hidden` on the rows.
 *
 * Hiding a row breaks breseq's striping, which build_rows computed by index over every row,
 * so the visible rows are re-striped by visible index after every change -- with the same
 * rule: a polymorphic row keeps its green and takes no stripe, but still counts. The red of
 * an ancestral row is a separate class and is left alone.
 *
 * Its own file rather than breseq_table.js: that one is the row markup's handler and loads
 * on every page that renders a breseq row, three of which have no menu. This needs a
 * configured container, and a whole table to re-stripe.
 *
 * `hidden` is the plain attribute, as breseq_table.js uses, not Bootstrap's .hidden.
 */
(function () {
    "use strict";

    var KEY_PREFIX = "mutation_matrix.references.";
    var STRIPES = ["alternate_table_row_0", "alternate_table_row_1"];

    function init(container) {
        var list = container.querySelector('[data-role="references"]');
        var table = document.querySelector("table.breseq-table");
        if (!list || !table || !table.tBodies.length) { return; }
        var tbody = table.tBodies[0];
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-seq-id]"));
        var experimentId = container.getAttribute("data-experiment-id");
        var key = experimentId ? KEY_PREFIX + experimentId : null;
        var prefs = window.mutintPreferences({
            authenticated: container.getAttribute("data-authenticated") === "1",
            url: container.getAttribute("data-preferences-url"),
            embedded: window.mutintPreferences.embedded("breseq-references-prefs")
        });
        var hidden = key ? window.mutintPreferences.hiddenSet(prefs.get(key, null)) : {};

        // The row that says nothing is showing, made here rather than in the shared table
        // partial, which three other pages render without this menu.
        var empty = document.createElement("tr");
        empty.setAttribute("data-role", "references-empty");
        var cell = document.createElement("td");
        cell.colSpan = table.querySelectorAll("thead th").length;
        cell.textContent = container.getAttribute("data-empty-message") || "No mutations to show.";
        empty.appendChild(cell);
        empty.hidden = true;
        tbody.appendChild(empty);

        function apply() {
            var shown = 0;
            rows.forEach(function (tr) {
                var on = !hidden[tr.getAttribute("data-seq-id")];
                tr.hidden = !on;
                if (!on) { return; }
                tr.classList.remove(STRIPES[0], STRIPES[1]);
                if (!tr.classList.contains("polymorphism_table_row")) {
                    tr.classList.add(STRIPES[shown % 2]);
                }
                shown += 1;
            });
            empty.hidden = shown > 0 || rows.length === 0;
        }

        Array.prototype.forEach.call(list.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hidden[li.getAttribute("data-value")]);
        });
        var counter = container.querySelector('[data-role="reference-count"]');
        var picker = window.mutintSelectList(list, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var ref = li.getAttribute("data-value");
                    if (picker.isSelected(li)) { delete hidden[ref]; } else { hidden[ref] = true; }
                });
                if (counter) { counter.textContent = picker.count(); }
                apply();
                if (key) { prefs.set(key, { hidden: Object.keys(hidden) }); }
            }
        });
        if (counter) { counter.textContent = picker.count(); }
        Array.prototype.forEach.call(container.querySelectorAll("[data-references]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-references") === "all";
                picker.select(function () { return all; });
            });
        });
        apply();
        container.breseqReferences = { references: picker, apply: apply };
    }

    document.addEventListener("DOMContentLoaded", function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-breseq-references]"), init);
    });
}());

/* The Columns menu on the per-sample Mutations page: which of the table's columns show.
 *
 * The matrix's Columns menu (mutation_matrix/_columns_menu.html) over this table's own
 * columns, including the gene lists -- Genes inactivated and the rest -- which the
 * table renders hidden. The matrix is a DataTable and hides a column through it; this table is
 * server-rendered, so every header and cell carries its column as `data-column` and the menu
 * sets `hidden` on them all.
 *
 * Remembered as `breseq_table.columns`, not Compare's key: the two tables offer different
 * columns and different defaults. Stored as `{hidden, shown}` and read through
 * `columnHiddenSet`, so a column added later takes its own default (mutint_preferences.js).
 *
 * Its own file for breseq_references.js's reason: the row handler in breseq_table.js loads on
 * three pages that render breseq rows and have no menu.
 */
(function () {
    "use strict";

    var KEY = "breseq_table.columns";

    function init(container) {
        var list = container.querySelector('[data-role="columns"]');
        var table = document.querySelector("table.breseq-table");
        if (!list || !table) { return; }
        var prefs = window.mutintPreferences({
            authenticated: container.getAttribute("data-authenticated") === "1",
            url: container.getAttribute("data-preferences-url"),
            embedded: window.mutintPreferences.embedded("breseq-references-prefs")
        });
        // The defaults are what the server rendered the menu with.
        var defaults = {};
        Array.prototype.forEach.call(list.querySelectorAll("li[data-value]"), function (li) {
            defaults[li.getAttribute("data-value")] = li.classList.contains("active");
        });
        var hidden = window.mutintPreferences.columnHiddenSet(prefs.get(KEY, null), defaults);

        function apply() {
            Array.prototype.forEach.call(table.querySelectorAll("[data-column]"), function (cell) {
                var key = cell.getAttribute("data-column");
                if (Object.prototype.hasOwnProperty.call(defaults, key)) { cell.hidden = !!hidden[key]; }
            });
        }

        Array.prototype.forEach.call(list.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hidden[li.getAttribute("data-value")]);
        });
        var picker = window.mutintSelectList(list, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var key = li.getAttribute("data-value");
                    if (picker.isSelected(li)) { delete hidden[key]; } else { hidden[key] = true; }
                });
                apply();
                prefs.set(KEY, window.mutintPreferences.columnChoice(hidden, defaults));
            }
        });
        apply();
    }

    document.addEventListener("DOMContentLoaded", function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-breseq-columns]"), init);
    });
}());

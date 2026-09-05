/* A highlight-based multiselect over a list of rows.
 *
 * The shape is the genome browser's sample menu: rows are `<li data-value="..."><a>`, and
 * `active` on the <li> IS the selection -- there is no checkbox to disagree with it, and no
 * second place holding the same answer. What this adds is the gestures a list normally has:
 *
 *   click                  select only that row
 *   ctrl/cmd-click         toggle that row, keep the rest
 *   shift-click            the range from the anchor to here, replacing the selection
 *   ctrl/cmd + shift-click add that range to the selection
 *
 * `{toggle: true}` swaps the plain click for the ctrl/cmd one, so every click toggles the row
 * it lands on and shift-click adds a range rather than replacing the selection. That is the
 * behavior of a list of checkboxes, and it suits a list whose rows are independent of each
 * other -- the genome browser's sample menu, where each row is a BAM that is either loaded or
 * not, and where "select only this one" silently unloads everything else. The default stays
 * select-only for the lists that are choosing *a* set rather than ticking members of one.
 *
 * The anchor is the last row picked by a plain or ctrl-click. Arrow-key navigation is
 * deliberately not here; "Select all" and "Select none" are what a long list needs, and they
 * are also what makes a stray plain click cheap to undo.
 *
 * The rows are <a href="#"> rather than plain text so a list living inside a Bootstrap
 * dropdown gets that dropdown's own row styling for free. That is why the click handler stops
 * the default, and why it stops propagation too: Bootstrap's data-api closes a dropdown on any
 * click inside it, and picking rows is the whole point of these.
 */
(function () {
    "use strict";

    function rowsOf(list) {
        return Array.prototype.filter.call(list.children, function (node) {
            return node.tagName === "LI" && node.hasAttribute("data-value");
        });
    }

    /* list      the <ul> of rows
     * options   onChange(changedRows)  after any change, with only the rows that moved
     *           controls               element holding [data-select="all"|"none"] buttons and
     *                                  an optional .aledb-select-count; defaults to the list's
     *                                  parent, which is what `select_list.html` renders
     *           toggle                 plain click toggles one row instead of replacing the
     *                                  selection; see the note above
     */
    window.aledbSelectList = function (list, options) {
        options = options || {};
        var onChange = options.onChange || function () {};
        var controls = options.controls === undefined ? list.parentNode : options.controls;
        var rows = rowsOf(list);
        var toggleMode = !!options.toggle;
        var anchor = null;

        function isOn(row) { return row.classList.contains("active"); }

        // The one writer of the class, so the highlight and whatever a caller hangs off it can
        // never disagree. Returns the row only when it actually moved -- the genome browser
        // loads a BAM per changed row and must not reload one that was already showing.
        function flip(row, on) {
            if (isOn(row) === on) { return null; }
            row.classList.toggle("active", on);
            return row;
        }

        function selectedRows() { return rows.filter(isOn); }

        function fire(changed) {
            changed = changed.filter(Boolean);
            showCount();
            if (changed.length) { onChange(changed); }
            return changed;
        }

        function showCount() {
            if (!controls) { return; }
            var label = controls.querySelector(".aledb-select-count");
            if (label) {
                label.textContent = selectedRows().length + " of " + rows.length + " selected";
            }
        }

        function setAll(wanted) {
            return rows.map(function (row) { return flip(row, wanted(row)); });
        }

        function spanBetween(from, to) {
            var a = rows.indexOf(from), b = rows.indexOf(to);
            if (a < 0) { return [to]; }
            return rows.slice(Math.min(a, b), Math.max(a, b) + 1);
        }

        list.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var row = event.target.closest("li");
            // indexOf as well as the attribute check: a list nested inside another <li> -- a
            // Bootstrap dropdown is exactly that -- would otherwise let `closest` climb out of
            // this list and return a row belonging to something else.
            if (!row || rows.indexOf(row) < 0) { return; }

            // In toggle mode every click is the additive one: there is no gesture that
            // replaces the whole selection, because that is the gesture this mode exists to
            // remove. The presets still set the selection outright.
            var additive = toggleMode || event.ctrlKey || event.metaKey;
            var changed;
            if (event.shiftKey) {
                var span = spanBetween(anchor || rows[0], row);
                changed = additive
                    ? span.map(function (r) { return flip(r, true); })
                    : setAll(function (r) { return span.indexOf(r) >= 0; });
                // The anchor deliberately stays put, so a second shift-click re-aims the same
                // range rather than growing it from wherever the last one landed.
            } else if (additive) {
                changed = [flip(row, !isOn(row))];
                anchor = row;
            } else {
                changed = setAll(function (r) { return r === row; });
                anchor = row;
            }
            fire(changed);
        });

        var api = {
            rows: function () { return rows.slice(); },
            isSelected: isOn,
            count: function () { return selectedRows().length; },
            selected: function () {
                return selectedRows().map(function (r) { return r.getAttribute("data-value"); });
            },
            // Set the whole selection to the rows `wanted` accepts. That is what makes the
            // preset buttons a set of alternatives rather than a set of additions.
            select: function (wanted) { return fire(setAll(wanted)); },
            // `silent` is for a caller correcting the list from the outside -- the genome
            // browser's `trackremoved` handler, which is already inside the removal it would
            // otherwise be told to perform again.
            setSelected: function (row, on, silent) {
                var changed = flip(row, on);
                showCount();
                if (changed && !silent) { onChange([changed]); }
                return changed;
            },
            rowFor: function (value) {
                for (var i = 0; i < rows.length; i++) {
                    if (rows[i].getAttribute("data-value") === String(value)) { return rows[i]; }
                }
                return null;
            }
        };

        if (controls) {
            Array.prototype.forEach.call(
                controls.querySelectorAll("[data-select]"), function (button) {
                    var on = button.getAttribute("data-select") === "all";
                    button.addEventListener("click", function (event) {
                        event.preventDefault();
                        api.select(function () { return on; });
                    });
                });
        }
        showCount();
        return api;
    };
})();

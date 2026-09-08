/* The mutation matrix's client half: draw the rows as a DataTable, and run its two menus.
 *
 * The server rendered the header and handed the rows over as JSON (see
 * mutint_sample/mutation_matrix.py and mutation_matrix/_table.html). What happens here:
 *
 *   - the reader's remembered choices are read *before* the first draw -- from the page when
 *     they are signed in (the tag embedded them), from the browser's own storage when they are not -- so
 *     the table appears already the way they left it, with no flash of hidden columns;
 *   - each <th> becomes a DataTables column that reads its cell by name, `data: "gene"` or
 *     `data: "samples.3"`, so no index anywhere depends on which columns are showing;
 *   - a sample cell renders once, as its frequency carried in a CSS variable (`--f`) with the
 *     number inside, linked into the genome browser when the server gave it a URL and blank
 *     when the sample does not carry the mutation; the Frequency display menu only changes
 *     the table's `freq-<format>` class, and the stylesheet draws the number, a bar, a heat
 *     map or both from that one markup -- no redraw of a thousand rows;
 *   - a row whose mutation is in no *shown* sample, whose type or reference is hidden, or
 *     outside the row set the Show menu has chosen, is filtered out through DataTables' own
 *     search hook rather than by hiding row nodes -- so paging and the row count stay honest,
 *     and so Export's "Showing" is exactly what is on the screen;
 *   - the Columns, Samples, Types and References menus are mutintSelectList in toggle mode,
 *     the genome browser's sample menu four times over, and every change is saved back where
 *     it was read from (the store itself is mutint_preferences.js);
 *   - a row the server marked `ancestral` -- observed in the designated ancestor, drawn
 *     because the reader asked -- is tinted red on every draw, the per-sample table's tint;
 *   - the table lives in a scroll box, with DataTables' own controls above it: the header
 *     sticks to its top and the descriptive columns to its left, each pinned column's `left`
 *     being the sum of the widths before it, recomputed after every draw and whenever the
 *     table's size changes, because widths change with the data on the page;
 *   - nothing sorts. The rows arrive in breseq's order, reference then position, and a
 *     sample's header is a link to that sample's own page rather than a sort handle.
 *
 * Stored as the *hidden* set, not the visible one, so a column, sample or type that did not
 * exist when the choice was made shows by default rather than vanishing.
 *
 * Loaded from the page beside breseq_table.js; jQuery, DataTables (with Buttons) and
 * mutint_select_list.js come from base.html.
 */
(function () {
    "use strict";

    var COLUMNS_KEY = "mutation_matrix.columns";
    var TYPES_KEY = "mutation_matrix.types";
    var FREQUENCY_KEY = "mutation_matrix.frequency";
    var VIEW_KEY = "mutation_matrix.view";
    var SHOW_KEY = "mutation_matrix.show";
    var VIEWS = { normal: true, condensed: true };
    var FORMATS = { number: "Number", bars: "Bars", heat: "Heat map", both: "Number and heat map" };
    var SAMPLES_KEY_PREFIX = "mutation_matrix.samples.";
    // Per experiment, like samples: a contig name means something only within one reference
    // genome. Shared with the per-sample Mutations page (breseq_references.js).
    var REFERENCES_KEY_PREFIX = "mutation_matrix.references.";

    /* The reader's store, from mutint_preferences.js: the embedded choices and the save
       endpoint for a signed-in reader, the browser's own storage otherwise. */
    function storage(container, table) {
        return window.mutintPreferences({
            authenticated: container.getAttribute("data-authenticated") === "1",
            url: container.getAttribute("data-preferences-url"),
            embedded: window.mutintPreferences.embedded(table.id + "-prefs")
        });
    }
    var hiddenSet = window.mutintPreferences.hiddenSet;

    function keys(set) { return Object.keys(set); }

    function renderHtml(data) { return data === null || data === undefined ? "" : data; }

    /* A sample cell: filter and export on the text, display a bare percentage -- `100`,
       `42` -- with the full text as the title, so the column can be narrow. Linked when the
       server gave a URL. The frequency rides along as `--f` for the bar and heat formats; a
       present call with no frequency (a check mark) counts as 1 there. */
    function compact(cell) {
        if (typeof cell.s !== "number" || cell.f.indexOf("%") < 0) { return cell.f; }
        return String(Math.round(cell.s * 100));
    }
    function renderSample(cell, type) {
        if (!cell) { return ""; }
        if (type !== "display") { return cell.f; }
        var node = document.createElement(cell.u ? "a" : "span");
        if (cell.u) { node.href = cell.u; }
        node.className = "mutation-matrix-cell" + (cell.p ? " polymorphic" : "");
        node.style.setProperty("--f", typeof cell.s === "number" ? String(cell.s) : "1");
        node.title = cell.f + (cell.u ? " \u2014 view the pileup at this position" : "");
        var text = document.createElement("span");
        text.textContent = compact(cell);
        node.appendChild(text);
        return node.outerHTML;
    }

    function init(container) {
        var table = container.querySelector("table");
        var rowsNode = document.getElementById(table.id + "-rows");
        var rows = rowsNode ? JSON.parse(rowsNode.textContent) : [];
        var prefs = storage(container, table);
        var experimentId = container.getAttribute("data-experiment-id");
        var samplesKey = experimentId ? SAMPLES_KEY_PREFIX + experimentId : null;
        var referencesKey = experimentId ? REFERENCES_KEY_PREFIX + experimentId : null;

        var hiddenColumns = hiddenSet(prefs.get(COLUMNS_KEY, null));
        if (prefs.get(COLUMNS_KEY, null) === null) {
            // Nothing remembered yet: the server's defaults, read off the menu it rendered.
            Array.prototype.forEach.call(container.querySelectorAll('[data-role="columns"] li[data-value]'), function (li) {
                if (!li.classList.contains("active")) { hiddenColumns[li.getAttribute("data-value")] = true; }
            });
        }
        var hiddenSamples = samplesKey ? hiddenSet(prefs.get(samplesKey, null)) : {};
        var hiddenTypes = hiddenSet(prefs.get(TYPES_KEY, null));
        var hiddenReferences = referencesKey ? hiddenSet(prefs.get(referencesKey, null)) : {};
        var stored = prefs.get(FREQUENCY_KEY, null);
        var format = stored && FORMATS[stored.format] ? stored.format : "number";
        table.classList.add("freq-" + format);
        var storedView = prefs.get(VIEW_KEY, null);
        var view = storedView && VIEWS[storedView.view] ? storedView.view : "condensed";
        table.classList.add("view-" + view);
        // The row set to show, when this table offers the remembered one; All otherwise --
        // a choice made on a page that has sets must not empty one that has none.
        var showList = container.querySelector('[data-role="show"]');
        var storedShow = prefs.get(SHOW_KEY, null);
        var shownSet = "";
        if (showList && storedShow && storedShow.set &&
                showList.querySelector('li[data-value="' + storedShow.set + '"]')) {
            shownSet = storedShow.set;
        }

        var ths = Array.prototype.slice.call(table.querySelectorAll("thead th"));
        var shownSampleIndexes = {};
        var columns = ths.map(function (th) {
            var sample = th.getAttribute("data-sample");
            if (sample !== null) {
                var index = parseInt(th.getAttribute("data-index"), 10);
                var visible = !hiddenSamples[sample];
                if (visible) { shownSampleIndexes[index] = true; }
                // The header's population color, on every cell of the column too: the bar
                // format draws in it.
                var paletteClass = (th.className.match(/sample-palette-\d+/) || [""])[0];
                return {
                    data: "samples." + index,
                    defaultContent: "",
                    className: "breseq-sample " + paletteClass,
                    visible: visible,
                    render: renderSample
                };
            }
            var key = th.getAttribute("data-key");
            var column = {
                data: key,
                defaultContent: "",
                className: th.className,
                visible: !hiddenColumns[key],
                render: renderHtml
            };
            if (key === "seq_id") {
                column.render = function (data, type, row) {
                    if (type !== "display") { return renderHtml(data); }
                    if (!row.seq_id_url) { return renderHtml(data); }
                    var a = document.createElement("a");
                    a.href = row.seq_id_url;
                    if (row.seq_id_title) { a.title = row.seq_id_title; }
                    a.innerHTML = renderHtml(data);
                    return a.outerHTML;
                };
            }
            return column;
        });

        // The menus reflect the remembered state before anything is drawn.
        var columnList = container.querySelector('[data-role="columns"]');
        var sampleList = container.querySelector('[data-role="samples"]');
        var typeList = container.querySelector('[data-role="types"]');
        var referenceList = container.querySelector('[data-role="references"]');
        Array.prototype.forEach.call(columnList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hiddenColumns[li.getAttribute("data-value")]);
        });
        Array.prototype.forEach.call(sampleList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hiddenSamples[li.getAttribute("data-value")]);
        });
        Array.prototype.forEach.call(typeList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hiddenTypes[li.getAttribute("data-value")]);
        });
        Array.prototype.forEach.call(referenceList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hiddenReferences[li.getAttribute("data-value")]);
        });

        // Rows whose mutation is in no shown sample, whose type or reference is hidden, or
        // outside the chosen row set, leave the table -- through the search hook, so the count
        // and the pager describe what is visible.
        $.fn.dataTable.ext.search.push(function (settings, searchData, index, rowData) {
            if (settings.nTable !== table) { return true; }
            if (hiddenTypes[rowData.type]) { return false; }
            if (hiddenReferences[rowData.seq_id_text]) { return false; }
            if (shownSet && (rowData.sets || []).indexOf(shownSet) < 0) { return false; }
            var cells = rowData.samples || [];
            for (var i = 0; i < cells.length; i++) {
                if (cells[i] && shownSampleIndexes[i]) { return true; }
            }
            return false;
        });

        function indexOfKey(key) {
            for (var i = 0; i < ths.length; i++) { if (ths[i].getAttribute("data-key") === key) { return i; } }
            return -1;
        }
        function indexOfSample(id) {
            for (var i = 0; i < ths.length; i++) { if (ths[i].getAttribute("data-sample") === String(id)) { return i; } }
            return -1;
        }

        var dt = $(table).DataTable({
            data: rows,
            columns: columns,
            deferRender: true,
            autoWidth: false,
            paging: true,
            pagingType: "full_numbers",
            pageLength: 100,
            lengthMenu: [[50, 100, 500, 1000, -1], [50, 100, 500, 1000, "All"]],
            // The server's order, and no sort handles on the headers: a click on a sample's
            // header follows its link instead.
            ordering: false,
            // Two rows of controls above the table -- length, search and the count; then the
            // pager and Export CSV -- and the table alone in the box that scrolls. The box
            // itself is DataTables' doing, so that it wraps only the table.
            dom: '<"mutation-matrix-toolbar"lfi><"mutation-matrix-toolbar"pB>r<"mutation-matrix-scroll"t>',
            // Export is a menu of two: the rows showing -- after the Show menu, the hidden
            // samples and types, and the search box, which is what `search: "applied"` means
            // -- or every row the server produced. Visible columns only, either way. Ancestral
            // rows the reader asked to see are rows the server produced, so both include them;
            // they are in no row set, so choosing one in the Show menu drops them.
            buttons: [{
                extend: "collection",
                text: "Export CSV",
                autoClose: true,
                buttons: [{
                    extend: "csv",
                    text: "Filtered mutations",
                    title: (container.getAttribute("data-csv-title") || "mutations") + "_showing",
                    exportOptions: { columns: ":visible", modifier: { search: "applied" } }
                }, {
                    extend: "csv",
                    text: "All mutations",
                    title: container.getAttribute("data-csv-title") || "mutations",
                    exportOptions: { columns: ":visible", modifier: { search: "none" } }
                }]
            }],
            language: { emptyTable: container.getAttribute("data-empty-message") || "No mutations to show." },
            // breseq shades by displayed row, so a filtered table stripes like a full one.
            // A row the server marked ancestral gets the per-sample table's red -- toggled
            // rather than added, because deferRender reuses row nodes across draws.
            rowCallback: function (row, data, displayIndex) {
                row.classList.remove("alternate_table_row_0", "alternate_table_row_1", "odd", "even");
                row.classList.add("alternate_table_row_" + (displayIndex % 2));
                row.classList.toggle("ancestral_table_row", !!data.ancestral);
            },
            // `dt` is not assigned until the constructor returns, and the first draw happens
            // inside it; the explicit call below covers that draw.
            drawCallback: function () { if (dt) { pinColumns(); } }
        });

        /* The descriptive columns stay put while the samples scroll. `position: sticky`
           needs each pinned column's `left` to be the width of everything pinned before it,
           and widths change with the page's data, so this runs after every draw -- and again
           whenever the table's size changes, which is how a late font or a resized window
           reach it. */
        var scrollBox = container.querySelector(".mutation-matrix-scroll");
        function pinColumns() {
            var left = 0;
            ths.forEach(function (th, i) {
                if (th.getAttribute("data-key") === null) { return; }
                var column = dt.column(i);
                var nodes = [th].concat(Array.prototype.slice.call(column.nodes()));
                if (!column.visible()) {
                    nodes.forEach(function (el) { el.classList.remove("pinned"); el.style.left = ""; });
                    return;
                }
                nodes.forEach(function (el) { el.classList.add("pinned"); el.style.left = left + "px"; });
                left += th.getBoundingClientRect().width;
            });
        }

        /* The box reaches the bottom of the window, whatever sits above it on the page, so
           its scrollbars sit at the window's edges (the stylesheet takes care of the sides). */
        function sizeScrollBox() {
            if (!scrollBox) { return; }
            var root = document.documentElement;
            var top = scrollBox.getBoundingClientRect().top + window.pageYOffset;
            var height = Math.max(240, Math.floor(root.clientHeight - top));
            scrollBox.style.maxHeight = height + "px";
            // Anything left under the box -- a fraction of a pixel, something a deployment
            // put at the page's foot -- gives the page a scrollbar of its own with nothing
            // to scroll. Measure what is left and take it off the box instead.
            var excess = root.scrollHeight - root.clientHeight;
            if (excess > 0 && height - excess >= 240) { scrollBox.style.maxHeight = (height - excess) + "px"; }
        }
        sizeScrollBox();
        window.addEventListener("resize", sizeScrollBox);
        // Once more when everything has loaded: an image above the box arriving after this
        // ran moves the box's top, and a box sized before that overflows the page.
        window.addEventListener("load", sizeScrollBox);
        pinColumns();
        if (window.ResizeObserver) {
            new ResizeObserver(function () { pinColumns(); }).observe(table);
            // Whatever moves the box's top after this ran -- a logo that loaded late, a
            // filter summary that wrapped -- changes the body's height, and the box follows.
            new ResizeObserver(function () { sizeScrollBox(); }).observe(document.body);
        }

        var counter = container.querySelector('[data-role="sample-count"]');
        var columnPicker = window.mutintSelectList(columnList, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var key = li.getAttribute("data-value"), on = columnPicker.isSelected(li);
                    dt.column(indexOfKey(key)).visible(on, false);
                    if (on) { delete hiddenColumns[key]; } else { hiddenColumns[key] = true; }
                });
                dt.columns.adjust().draw(false);
                pinColumns();
                prefs.set(COLUMNS_KEY, { hidden: keys(hiddenColumns) });
            }
        });
        var typeCounter = container.querySelector('[data-role="type-count"]');
        var typePicker = window.mutintSelectList(typeList, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var type = li.getAttribute("data-value");
                    if (typePicker.isSelected(li)) { delete hiddenTypes[type]; } else { hiddenTypes[type] = true; }
                });
                if (typeCounter) { typeCounter.textContent = typePicker.count(); }
                dt.draw();
                prefs.set(TYPES_KEY, { hidden: keys(hiddenTypes) });
            }
        });
        if (typeCounter) { typeCounter.textContent = typePicker.count(); }
        Array.prototype.forEach.call(container.querySelectorAll("[data-types]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-types") === "all";
                typePicker.select(function () { return all; });
            });
        });
        var referenceCounter = container.querySelector('[data-role="reference-count"]');
        var referencePicker = window.mutintSelectList(referenceList, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var ref = li.getAttribute("data-value");
                    if (referencePicker.isSelected(li)) { delete hiddenReferences[ref]; } else { hiddenReferences[ref] = true; }
                });
                if (referenceCounter) { referenceCounter.textContent = referencePicker.count(); }
                dt.draw();
                if (referencesKey) { prefs.set(referencesKey, { hidden: keys(hiddenReferences) }); }
            }
        });
        if (referenceCounter) { referenceCounter.textContent = referencePicker.count(); }
        Array.prototype.forEach.call(container.querySelectorAll("[data-references]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-references") === "all";
                referencePicker.select(function () { return all; });
            });
        });
        var samplePicker = window.mutintSelectList(sampleList, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var id = li.getAttribute("data-value"), on = samplePicker.isSelected(li);
                    var th = ths[indexOfSample(id)];
                    var index = parseInt(th.getAttribute("data-index"), 10);
                    dt.column(indexOfSample(id)).visible(on, false);
                    if (on) { shownSampleIndexes[index] = true; delete hiddenSamples[id]; }
                    else { delete shownSampleIndexes[index]; hiddenSamples[id] = true; }
                });
                if (counter) { counter.textContent = samplePicker.count(); }
                // A full draw: which rows show depends on which samples do.
                dt.columns.adjust().draw();
                if (samplesKey) {
                    prefs.set(samplesKey, { hidden: keys(hiddenSamples).map(Number) });
                }
            }
        });
        if (counter) { counter.textContent = samplePicker.count(); }

        Array.prototype.forEach.call(container.querySelectorAll("[data-samples]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-samples") === "all";
                samplePicker.select(function () { return all; });
            });
        });

        /* The Frequency display menu: rendered in the container, moved to the front of
           DataTables' first toolbar row. Choosing a format swaps one class on the table. */
        var frequencyControl = container.querySelector('[data-role="frequency-control"]');
        var frequencyList = container.querySelector('[data-role="frequency"]');
        var frequencyLabel = container.querySelector('[data-role="frequency-label"]');
        var legend = container.querySelector('[data-role="frequency-legend"]');
        var toolbar = container.querySelector(".mutation-matrix-toolbar");
        if (toolbar && frequencyControl) { toolbar.insertBefore(frequencyControl, toolbar.firstChild); }
        function showFormat(name) {
            Object.keys(FORMATS).forEach(function (key) { table.classList.toggle("freq-" + key, key === name); });
            if (frequencyLabel) { frequencyLabel.textContent = FORMATS[name]; }
            if (legend) { legend.hidden = !(name === "heat" || name === "both"); }
        }
        Array.prototype.forEach.call(frequencyList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", li.getAttribute("data-value") === format);
        });
        showFormat(format);
        var frequencyPicker = window.mutintSelectList(frequencyList, {
            controls: null,
            onChange: function () {
                var chosen = frequencyList.querySelector("li.active");
                format = chosen ? chosen.getAttribute("data-value") : "number";
                showFormat(format);
                prefs.set(FREQUENCY_KEY, { format: format });
            }
        });

        /* The Show menu: All, or one of the row sets the server offered. One class of row
           filter beside the samples' and the types', through the same search hook. */
        var showLabel = container.querySelector('[data-role="show-label"]');
        var showPicker = null;
        if (showList) {
            Array.prototype.forEach.call(showList.querySelectorAll("li[data-value]"), function (li) {
                li.classList.toggle("active", li.getAttribute("data-value") === shownSet);
            });
            var showName = function () {
                var chosen = showList.querySelector("li.active a");
                if (showLabel) { showLabel.textContent = chosen ? chosen.textContent.replace(/\s*\(\d+\)\s*$/, "") : "All"; }
            };
            showName();
            showPicker = window.mutintSelectList(showList, {
                controls: null,
                onChange: function () {
                    var chosen = showList.querySelector("li.active");
                    shownSet = chosen ? chosen.getAttribute("data-value") : "";
                    showName();
                    dt.draw();
                    prefs.set(SHOW_KEY, { set: shownSet });
                }
            });
        }

        /* The View switch: Normal is the Mutations page's cell padding, Condensed one line
           per row. One class on the table, and the pinned offsets recomputed, since the
           descriptive columns' widths move with their padding. */
        var viewControl = container.querySelector('[data-role="view-control"]');
        var toolbars = container.querySelectorAll(".mutation-matrix-toolbar");
        if (viewControl && toolbars.length > 1) { toolbars[1].appendChild(viewControl); }
        function showView(name) {
            Object.keys(VIEWS).forEach(function (key) { table.classList.toggle("view-" + key, key === name); });
            Array.prototype.forEach.call(container.querySelectorAll("[data-view]"), function (button) {
                button.classList.toggle("active", button.getAttribute("data-view") === name);
            });
            pinColumns();
        }
        showView(view);
        Array.prototype.forEach.call(container.querySelectorAll("[data-view]"), function (button) {
            button.addEventListener("click", function () {
                view = button.getAttribute("data-view");
                showView(view);
                prefs.set(VIEW_KEY, { view: view });
            });
        });

        // For a harness or a console: the DataTable behind the container.
        container.mutationMatrix = { table: dt, columns: columnPicker, samples: samplePicker, types: typePicker, references: referencePicker, frequency: frequencyPicker, show: showPicker };
    }

    $(function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-mutation-matrix]"), init);
    });
}());

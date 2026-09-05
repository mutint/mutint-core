/* The mutation matrix's client half: draw the rows as a DataTable, and run its two menus.
 *
 * The server rendered the header and handed the rows over as JSON (see
 * mutint_sample/mutation_matrix.py and mutation_matrix/_table.html). What happens here:
 *
 *   - the reader's remembered choices are read *before* the first draw -- from the page when
 *     they are signed in (the tag embedded them), from localStorage when they are not -- so
 *     the table appears already the way they left it, with no flash of hidden columns;
 *   - each <th> becomes a DataTables column that reads its cell by name, `data: "gene"` or
 *     `data: "samples.3"`, so no index anywhere depends on which columns are showing;
 *   - a sample cell renders as its frequency, linked into the genome browser when the server
 *     gave it a URL, and blank when the sample does not carry the mutation;
 *   - a row whose mutation is in no *shown* sample is filtered out, through DataTables' own
 *     search hook rather than by hiding row nodes -- so paging and the row count stay honest;
 *   - the Columns and Samples menus are mutintSelectList in toggle mode, the genome browser's
 *     sample menu twice over, and every change is saved back where it was read from.
 *
 * Stored as the *hidden* set, not the visible one, so a column or sample that did not exist
 * when the choice was made shows by default rather than vanishing.
 *
 * Loaded from the page beside breseq_table.js; jQuery, DataTables (with Buttons) and
 * mutint_select_list.js come from base.html.
 */
(function () {
    "use strict";

    var COLUMNS_KEY = "mutation_matrix.columns";
    var SAMPLES_KEY_PREFIX = "mutation_matrix.samples.";

    /* One get/set pair over whichever store this reader has. */
    function storage(container, table) {
        var authed = container.getAttribute("data-authenticated") === "1";
        var url = container.getAttribute("data-preferences-url");
        var embedded = {};
        if (authed) {
            var node = document.getElementById(table.id + "-prefs");
            if (node) { try { embedded = JSON.parse(node.textContent) || {}; } catch (e) { embedded = {}; } }
        }
        return {
            get: function (key, fallback) {
                if (authed) { return Object.prototype.hasOwnProperty.call(embedded, key) ? embedded[key] : fallback; }
                try {
                    var raw = window.localStorage.getItem("mutint." + key);
                    return raw ? JSON.parse(raw) : fallback;
                } catch (e) { return fallback; }
            },
            set: function (key, value) {
                if (authed) {
                    // Fire and forget: the page already shows the choice, and a save that
                    // failed costs a reload's worth of memory, not correctness.
                    window.mutintPostJson(url, { key: key, value: value }).catch(function () {});
                    return;
                }
                try { window.localStorage.setItem("mutint." + key, JSON.stringify(value)); } catch (e) { /* private mode, quota */ }
            }
        };
    }

    function hiddenSet(stored) {
        var hidden = stored && Array.isArray(stored.hidden) ? stored.hidden : [];
        var set = {};
        hidden.forEach(function (value) { set[String(value)] = true; });
        return set;
    }

    function keys(set) { return Object.keys(set); }

    function renderHtml(data) { return data === null || data === undefined ? "" : data; }

    /* A sample cell by DataTables' four purposes: sort by frequency (absent last), filter and
       type on the text, display the text -- linked when the server gave a URL. */
    function renderSample(cell, type) {
        if (!cell) { return type === "sort" ? -1 : ""; }
        if (type === "sort") { return cell.s; }
        if (type !== "display") { return cell.f; }
        if (!cell.u) { return cell.f; }
        var a = document.createElement("a");
        a.href = cell.u;
        a.title = "View the pileup at this position";
        a.textContent = cell.f;
        return a.outerHTML;
    }

    function init(container) {
        var table = container.querySelector("table");
        var rowsNode = document.getElementById(table.id + "-rows");
        var rows = rowsNode ? JSON.parse(rowsNode.textContent) : [];
        var prefs = storage(container, table);
        var experimentId = container.getAttribute("data-experiment-id");
        var samplesKey = experimentId ? SAMPLES_KEY_PREFIX + experimentId : null;

        var hiddenColumns = hiddenSet(prefs.get(COLUMNS_KEY, null));
        if (prefs.get(COLUMNS_KEY, null) === null) {
            // Nothing remembered yet: the server's defaults, read off the menu it rendered.
            Array.prototype.forEach.call(container.querySelectorAll('[data-role="columns"] li[data-value]'), function (li) {
                if (!li.classList.contains("active")) { hiddenColumns[li.getAttribute("data-value")] = true; }
            });
        }
        var hiddenSamples = samplesKey ? hiddenSet(prefs.get(samplesKey, null)) : {};

        var ths = Array.prototype.slice.call(table.querySelectorAll("thead th"));
        var shownSampleIndexes = {};
        var columns = ths.map(function (th) {
            var sample = th.getAttribute("data-sample");
            if (sample !== null) {
                var index = parseInt(th.getAttribute("data-index"), 10);
                var visible = !hiddenSamples[sample];
                if (visible) { shownSampleIndexes[index] = true; }
                return {
                    data: "samples." + index,
                    defaultContent: "",
                    className: "breseq-sample",
                    visible: visible,
                    render: renderSample,
                    createdCell: function (td, cell) {
                        if (cell) { td.classList.add(cell.p ? "polymorphic" : "present"); }
                    }
                };
            }
            var key = th.getAttribute("data-key"), sortKey = th.getAttribute("data-sort-key");
            var column = {
                data: sortKey ? { _: key, sort: sortKey, filter: key, display: key } : key,
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
        Array.prototype.forEach.call(columnList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hiddenColumns[li.getAttribute("data-value")]);
        });
        Array.prototype.forEach.call(sampleList.querySelectorAll("li[data-value]"), function (li) {
            li.classList.toggle("active", !hiddenSamples[li.getAttribute("data-value")]);
        });

        // Rows whose mutation is in no shown sample leave the table -- through the search
        // hook, so the count and the pager describe what is visible.
        $.fn.dataTable.ext.search.push(function (settings, searchData, index, rowData) {
            if (settings.nTable !== table) { return true; }
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

        var order = [];
        if (indexOfKey("seq_id") >= 0) { order.push([indexOfKey("seq_id"), "asc"]); }
        if (indexOfKey("position") >= 0) { order.push([indexOfKey("position"), "asc"]); }

        var dt = $(table).DataTable({
            data: rows,
            columns: columns,
            deferRender: true,
            autoWidth: false,
            paging: true,
            pagingType: "full_numbers",
            pageLength: 100,
            lengthMenu: [50, 100, 500, 1000],
            order: order,
            dom: 'l<"pull-left"B><"pull-right"f>rt<<"pull-left"i><"pull-right"p>>',
            buttons: [{
                extend: "csv",
                text: "CSV",
                title: container.getAttribute("data-csv-title") || "mutations",
                exportOptions: { columns: ":visible" }
            }],
            language: { emptyTable: container.getAttribute("data-empty-message") || "No mutations to show." },
            // breseq shades by displayed row, so a filtered table stripes like a full one.
            rowCallback: function (row, data, displayIndex) {
                row.classList.remove("alternate_table_row_0", "alternate_table_row_1", "odd", "even");
                row.classList.add("alternate_table_row_" + (displayIndex % 2));
            }
        });

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
                prefs.set(COLUMNS_KEY, { hidden: keys(hiddenColumns) });
            }
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

        // For a harness or a console: the DataTable behind the container.
        container.mutationMatrix = { table: dt, columns: columnPicker, samples: samplePicker };
    }

    $(function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-mutation-matrix]"), init);
    });
}());

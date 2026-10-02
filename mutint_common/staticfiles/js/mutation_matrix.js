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
 *   - on Compare (mutation_matrix/page.html), everything else is decided here as well -- see
 *     "Compare decides in the browser" below;
 *   - the menus sit in a tab strip above the table (on Compare: Treatments, Populations,
 *     Samples, Time Points, Mutations, References, Columns, Frequencies, Sets, Export;
 *     Search has the matrix's own six),
 *     found through `[data-mutation-matrix-controls]` because the page may own the strip;
 *     the DataTables toolbar -- length, search, count, pager, in one row -- stays under the
 *     strip on every tab, and the Export buttons are moved into the Export pane;
 *   - a collapse bar under the panes, the sidebar's strip laid flat, folds away everything
 *     above it -- the panes, the strip, the page header -- and remembers that it did
 *     (`mutation_matrix.options`); shown unless told otherwise;
 *   - the table lives in a scroll box as wide as itself and no wider than the window: the
 *     header sticks to its top and the descriptive columns to its left, each pinned column's
 *     `left` being the sum of the widths before it, recomputed after every draw and whenever
 *     the table's size changes, because widths change with the data on the page;
 *   - each pinned column has a handle on its right edge: dragged to set the column's width,
 *     double-clicked to reset it, remembered as `mutation_matrix.widths`;
 *   - nothing sorts. The rows arrive in breseq's order, reference then position, and a
 *     sample's header is a link to that sample's own page rather than a sort handle.
 *
 * Samples, types and references are stored as the *hidden* set, not the visible one, so one
 * that did not exist when the choice was made shows by default rather than vanishing. Columns
 * are stored as both lists, so a column added later takes its own default -- which for
 * Description and the gene lists is hidden (see mutint_preferences.js).
 *
 * **Compare decides in the browser.** Its page sends every sample and every call, unfiltered,
 * with the designated ancestor's rows flagged, and carries the reader's frequency range on its
 * Frequencies tab (`[data-role="view-filter"]`). Where that exists, every change runs `refresh`:
 *
 *   1. which sample columns show: ticked in Samples, and not hidden by the Populations,
 *      Treatments or Sample types menus or outside the Time range -- each a layer of its own,
 *      remembered per experiment, a sample hidden by a layer dimmed in the Samples menu;
 *   2. the data the table holds: the reader's frequency range drops cells (below the floor
 *      *or* above the ceiling; a call with no recorded frequency never), the ignored-gene rule
 *      drops rows (`gene_is_filtered`'s subset rule) where the page offers the gene box --
 *      Compare does not, for now -- the ancestral rows are dropped unless
 *      asked for, and a row left with no cell is no row -- so "All mutations" in Export is what
 *      the server used to produce, and the filter is written back to the session through
 *      `/filter/set` so every other page reads the same one;
 *   3. the row sets the page registered with `window.mutintMatrixSets` (Compare's Convergent
 *      and Fixed, compare_sets.js), asked what they hold over the shown samples and the kept
 *      cells of the non-ancestral rows, and counted in the Show menu;
 *   4. one draw.
 *
 * The sample columns are colored by population, by treatment or not at all -- one choice made
 * with the boxes on the Populations and Treatments tabs, one class on the table.
 *
 * Search has no such pane and none of this happens there.
 *
 * Loaded from the page beside breseq_table.js; jQuery, DataTables (with Buttons) and
 * mutint_select_list.js come from base.html.
 */
(function () {
    "use strict";

    /* The browser-side row sets: a page's script registers `fn(state, params)` under the key
       its `ClientSet` names, and gets `state = {samples: [{index, population, time}], rows:
       [{id, genes, cells}]}` -- the shown samples, and the rows a set may hold with only
       their kept cells -- and `params`, the values of the page's `[data-set-param]` inputs.
       It returns `{ids: [...], note: "...", error: "..."}`. Defined by whichever script loads
       first, so the page's may load before or after this one. */
    window.mutintMatrixSets = window.mutintMatrixSets || (function () {
        var registered = {};
        return {
            register: function (key, fn) { registered[key] = fn; },
            get: function (key) { return registered[key]; }
        };
    }());

    var COLUMNS_KEY = "mutation_matrix.columns";
    var TYPES_KEY = "mutation_matrix.types";
    var FREQUENCY_KEY = "mutation_matrix.frequency";
    var VIEW_KEY = "mutation_matrix.view";
    var SHOW_KEY = "mutation_matrix.show";
    var OPTIONS_KEY = "mutation_matrix.options";
    var WIDTHS_KEY = "mutation_matrix.widths";
    // How many rows a page shows -- "Show N entries" -- remembered like every other choice, so
    // a reload (Copy to ancestor makes one) does not drop "All" back to 100.
    var PAGE_LENGTH_KEY = "mutation_matrix.page_length";
    var PAGE_LENGTHS = [50, 100, 500, 1000, -1];
    // Where the reader was, carried across the reload Copy to ancestor makes. Session storage:
    // it is about this tab, this once, and is taken as soon as it is read.
    var RETURN_KEY = "mutation_matrix.return";
    var MIN_COLUMN_WIDTH = 40;
    var VIEWS = { normal: true, condensed: true };
    var FORMATS = { number: "Number", bars: "Bars", heat: "Heat map", both: "Number and heat map" };
    var SAMPLES_KEY_PREFIX = "mutation_matrix.samples.";
    // Per experiment, like samples: a contig name means something only within one reference
    // genome. Shared with the per-sample Mutations page (breseq_references.js).
    var REFERENCES_KEY_PREFIX = "mutation_matrix.references.";
    // Compare's layers over the samples, and its row sets' parameters: per experiment, since a
    // population, a treatment or a time point means something only within one.
    var POPULATIONS_KEY_PREFIX = "mutation_matrix.populations.";
    var TREATMENTS_KEY_PREFIX = "mutation_matrix.treatments.";
    var SAMPLE_TYPES_KEY_PREFIX = "mutation_matrix.sample_types.";
    var TIME_KEY_PREFIX = "mutation_matrix.time.";
    var SET_PARAMS_KEY_PREFIX = "mutation_matrix.set_params.";
    // How the sample columns are colored -- by population, by treatment, or not at all.
    var COLOR_KEY_PREFIX = "mutation_matrix.color.";
    var COLOR_MODES = { population: true, treatment: true, none: true };

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

    /* A column choice saved as a bare hidden set, before columns could be hidden by default
       other than Description. Description absent from it was shown on purpose; every column
       added since takes its default. */
    function legacyColumnChoice(stored) {
        if (!stored || Array.isArray(stored.shown)) { return stored; }
        var hidden = Array.isArray(stored.hidden) ? stored.hidden : [];
        return { hidden: hidden, shown: hidden.indexOf("description") < 0 ? ["description"] : [] };
    }

    /* A gene-list column (genes_inactivated and the rest, GENE_LIST_FIELDS): the markup to
       display, and the plain list -- which the server sends beside it as `<key>_text` -- for
       searching and exporting, since a long list is collapsed behind a Show button. */
    function renderGeneList(key) {
        return function (data, type, row) {
            if (type === "display") { return renderHtml(data); }
            return renderHtml(row[key + "_text"]);
        };
    }

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
        // Export as displayed: the CSV has always carried the bare number a cell shows.
        if (type !== "display" && type !== "export") { return cell.f; }
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
        // The menus sit in the tab strip's panes: the page's own when it rendered the strip
        // (mutation_matrix/page.html, with tabs of its own around these), the container's otherwise.
        var controls = document.querySelector('[data-mutation-matrix-controls="' + table.id + '"]') || container;
        var rowsNode = document.getElementById(table.id + "-rows");
        var rows = rowsNode ? JSON.parse(rowsNode.textContent) : [];
        var prefs = storage(container, table);
        var experimentId = container.getAttribute("data-experiment-id");
        function perExperiment(prefix) { return experimentId ? prefix + experimentId : null; }
        var samplesKey = perExperiment(SAMPLES_KEY_PREFIX);
        var referencesKey = perExperiment(REFERENCES_KEY_PREFIX);
        var populationsKey = perExperiment(POPULATIONS_KEY_PREFIX);
        var treatmentsKey = perExperiment(TREATMENTS_KEY_PREFIX);
        var sampleTypesKey = perExperiment(SAMPLE_TYPES_KEY_PREFIX);
        var timeKey = perExperiment(TIME_KEY_PREFIX);
        var setParamsKey = perExperiment(SET_PARAMS_KEY_PREFIX);

        // The rows as the server sent them; `samples` is what the table draws, and on Compare
        // it is these cells less the ones the reader's frequency range drops.
        rows.forEach(function (row) { row._cells = row.samples; row._sets = row.sets || []; });
        var allRows = rows;
        var filterBox = controls.querySelector('[data-role="view-filter"]');
        var decidesHere = !!filterBox;
        var viewFilter = { min_freq: null, max_freq: null, genes: [] };
        var ancestralShown = true;
        if (filterBox) {
            var stateNode = document.getElementById(filterBox.getAttribute("data-state-id"));
            if (stateNode) { viewFilter = JSON.parse(stateNode.textContent); }
            ancestralShown = filterBox.getAttribute("data-ancestral-shown") === "1";
        }
        // The ignored-gene half of the reader's filter applies only where its box is on the
        // page. Compare does not offer it for now, so there the list stays in the session for
        // the pages that do, and filters nothing here.
        var applyGenes = !!controls.querySelector('[data-role="filter-genes"]');

        // The server's defaults, read off the menu it rendered; a column the stored choice does
        // not name takes its default (mutint_preferences.js says why).
        var columnDefaults = {};
        Array.prototype.forEach.call(controls.querySelectorAll('[data-role="columns"] li[data-value]'), function (li) {
            columnDefaults[li.getAttribute("data-value")] = li.classList.contains("active");
        });
        var hiddenColumns = window.mutintPreferences.columnHiddenSet(
            legacyColumnChoice(prefs.get(COLUMNS_KEY, null)), columnDefaults);
        var hiddenSamples = samplesKey ? hiddenSet(prefs.get(samplesKey, null)) : {};
        var hiddenPopulations = populationsKey ? hiddenSet(prefs.get(populationsKey, null)) : {};
        var hiddenTreatments = treatmentsKey ? hiddenSet(prefs.get(treatmentsKey, null)) : {};
        var hiddenSampleTypes = sampleTypesKey ? hiddenSet(prefs.get(sampleTypesKey, null)) : {};
        var storedTime = timeKey ? prefs.get(timeKey, null) : null;
        var timeRange = {
            min: storedTime && typeof storedTime.min === "number" ? storedTime.min : null,
            max: storedTime && typeof storedTime.max === "number" ? storedTime.max : null
        };
        var hiddenTypes = hiddenSet(prefs.get(TYPES_KEY, null));
        var hiddenReferences = referencesKey ? hiddenSet(prefs.get(referencesKey, null)) : {};
        var stored = prefs.get(FREQUENCY_KEY, null);
        var format = stored && FORMATS[stored.format] ? stored.format : "number";
        table.classList.add("freq-" + format);
        // The pane box the collapse bar folds, with everything above it. `controls` is the
        // container itself when no pane box was found, and folding that would fold the table.
        var optionPanes = controls !== container ? controls : container.querySelector(".tab-content");
        var storedOptions = prefs.get(OPTIONS_KEY, null);
        var optionsShown = !(storedOptions && storedOptions.hidden === true);
        /* Everything on the page above the pane box: its earlier siblings (the strip), and
           walking up to the content box, each ancestor's earlier siblings -- the page header
           with its button bar, and on Search the search form. Gathered when applied rather
           than once, so a late-arriving element above is folded too. */
        function foldable() {
            var found = [], el = optionPanes;
            while (el && el.id !== "mutint-content") {
                for (var sib = el.previousElementSibling; sib; sib = sib.previousElementSibling) { found.push(sib); }
                el = el.parentElement;
            }
            return found;
        }
        function applyOptions() {
            if (!optionPanes) { return; }
            [optionPanes].concat(foldable()).forEach(function (el) {
                el.classList.toggle("mutation-matrix-folded", !optionsShown);
            });
        }
        // Before the first draw, like every other remembered choice, so the scroll box is
        // sized against the page as it will stay.
        applyOptions();
        var storedView = prefs.get(VIEW_KEY, null);
        var view = storedView && VIEWS[storedView.view] ? storedView.view : "condensed";
        table.classList.add("view-" + view);
        // The row set to show, when this table offers the remembered one; All otherwise --
        // a choice made on a page that has sets must not empty one that has none.
        var showList = controls.querySelector('[data-role="show"]');
        var storedShow = prefs.get(SHOW_KEY, null);
        var shownSet = "";
        if (showList && storedShow && storedShow.set &&
                showList.querySelector('li[data-value="' + storedShow.set + '"]')) {
            shownSet = storedShow.set;
        }

        // The Curate menu's "Copy to ancestor": present only where the page has a Curate
        // column and the experiment designates an ancestor.
        var curateAncestorId = container.getAttribute("data-curate-ancestor-id");
        var curateCopyApply = container.getAttribute("data-curate-copy-apply");

        var storedLength = prefs.get(PAGE_LENGTH_KEY, null);
        var pageLength = storedLength && PAGE_LENGTHS.indexOf(storedLength.length) >= 0 ? storedLength.length : 100;

        var ths = Array.prototype.slice.call(table.querySelectorAll("thead th"));
        var shownSampleIndexes = {};
        // What each sample column is, read off its header: which layers can hide it.
        var sampleMeta = [];
        ths.forEach(function (th, column) {
            if (th.getAttribute("data-sample") === null) { return; }
            var time = th.getAttribute("data-time");
            sampleMeta.push({
                id: th.getAttribute("data-sample"),
                index: parseInt(th.getAttribute("data-index"), 10),
                column: column,
                population: th.getAttribute("data-population") || "",
                treatment: th.getAttribute("data-treatment") || "",
                time: time ? parseFloat(time) : null,
                clonal: th.getAttribute("data-clonal") !== "0"
            });
        });
        /* Which layer hides a sample, by the name of its tab, or "" when none does. The
           Samples menu's own choice is not a layer: it is the sample, one at a time. */
        function hiddenBy(meta) {
            if (hiddenSampleTypes[meta.clonal ? "clonal" : "mixed"]) { return "Samples tab's Sample types menu"; }
            if (hiddenPopulations[meta.population]) { return "Populations tab"; }
            if (hiddenTreatments[meta.treatment]) { return "Treatments tab"; }
            if (meta.time !== null && ((timeRange.min !== null && meta.time < timeRange.min) ||
                                       (timeRange.max !== null && meta.time > timeRange.max))) {
                return "Time Points tab";
            }
            return "";
        }
        function isShown(meta) { return !hiddenSamples[meta.id] && !hiddenBy(meta); }
        var metaByColumn = {};
        sampleMeta.forEach(function (meta) {
            metaByColumn[meta.column] = meta;
            if (isShown(meta)) { shownSampleIndexes[meta.index] = true; }
        });

        /* Step 2: the data the table holds -- see the header. Everywhere but Compare it is the
           rows as sent. */
        function cellKept(cell) {
            if (!cell) { return false; }
            if (cell.n) { return true; }
            if (viewFilter.min_freq !== null && cell.s < viewFilter.min_freq / 100) { return false; }
            if (viewFilter.max_freq !== null && cell.s > viewFilter.max_freq / 100) { return false; }
            return true;
        }
        /* `gene_is_filtered`: every gene the mutation touches is ignored -- subset, not
           intersection -- with the same set sizes compared first. */
        function geneFiltered(row) {
            if (!applyGenes) { return false; }
            var ignored = {}, ignoredCount = 0;
            (viewFilter.genes || []).forEach(function (g) { if (!ignored[g]) { ignored[g] = true; ignoredCount++; } });
            if (!ignoredCount) { return false; }
            var genes = {}, geneCount = 0;
            (row.genes || []).forEach(function (g) { if (!genes[g]) { genes[g] = true; geneCount++; } });
            if (ignoredCount < geneCount) { return false; }
            return Object.keys(genes).every(function (g) { return ignored[g]; });
        }
        function buildData() {
            if (!decidesHere) { return allRows; }
            var out = [];
            allRows.forEach(function (row) {
                if (row.ancestral && !ancestralShown) { return; }
                if (geneFiltered(row)) { return; }
                var any = false;
                row.samples = row._cells.map(function (cell) {
                    var kept = cellKept(cell);
                    if (kept) { any = true; }
                    return kept ? cell : null;
                });
                if (any) { out.push(row); }
            });
            return out;
        }
        var data = buildData();

        var columns = ths.map(function (th, column) {
            var sample = th.getAttribute("data-sample");
            if (sample !== null) {
                var index = parseInt(th.getAttribute("data-index"), 10);
                var visible = isShown(metaByColumn[column]);
                // The header's population color, on every cell of the column too: the bar
                // format draws in it.
                // And the treatment palette's, which colors the column instead when the reader
                // asks (the color boxes below).
                var paletteClass = (th.className.match(/sample-palette-\d+/) || [""])[0] + " " +
                    (th.className.match(/treatment-palette-\w+/) || [""])[0];
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
                render: th.classList.contains("breseq-gene-list") ? renderGeneList(key) : renderHtml
            };
            // The Type cell links into the genome browser with every sample carrying the
            // mutation shown -- the per-sample table's Type link, for a row that is a mutation.
            if (key === "type") {
                column.render = function (data, type, row) {
                    if (type !== "display" || !row.type_url) { return renderHtml(data); }
                    var a = document.createElement("a");
                    a.href = row.type_url;
                    a.title = "Show the read alignments of every sample carrying this mutation";
                    a.textContent = data;
                    return a.outerHTML;
                };
            }
            // A row's actions, for a reader who can curate: a caret opening Edit and Copy, and
            // "Copy to ancestor" where the experiment designates one and the row is not
            // already its. The links are the server's (`curate_edit_url`, `curate_copy_url`);
            // Copy is absent where no sample carries the row. Nothing in an export.
            if (key === "curate") {
                column.orderable = false;
                column.render = function (data, type, row) {
                    if (type !== "display" || !row.curate_edit_url) { return ""; }
                    var group = document.createElement("div");
                    group.className = "btn-group mutation-matrix-curate";
                    var toggle = document.createElement("button");
                    toggle.type = "button";
                    toggle.className = "btn btn-default btn-xs dropdown-toggle";
                    toggle.setAttribute("data-toggle", "dropdown");
                    toggle.setAttribute("aria-haspopup", "true");
                    toggle.setAttribute("aria-expanded", "false");
                    toggle.title = "Curate this mutation";
                    var caret = document.createElement("span");
                    caret.className = "caret";
                    toggle.appendChild(caret);
                    var menu = document.createElement("ul");
                    menu.className = "dropdown-menu";
                    [["Edit", row.curate_edit_url, "Edit this mutation in every sample that carries it"],
                     ["Copy", row.curate_copy_url, "Copy this mutation to other samples"]].forEach(function (item) {
                        if (!item[1]) { return; }
                        var li = document.createElement("li");
                        var a = document.createElement("a");
                        a.href = item[1];
                        a.textContent = item[0];
                        a.title = item[2];
                        li.appendChild(a);
                        menu.appendChild(li);
                    });
                    if (curateAncestorId && row.curate_copy_source && !row.ancestral) {
                        var li = document.createElement("li");
                        var a = document.createElement("a");
                        a.href = "#";
                        a.textContent = "Copy to ancestor";
                        a.title = "Add this mutation to the designated ancestor, which subtracts it from every sample";
                        a.setAttribute("data-copy-to-ancestor", row.id);
                        a.setAttribute("data-source", row.curate_copy_source);
                        li.appendChild(a);
                        menu.appendChild(li);
                    }
                    group.appendChild(toggle);
                    group.appendChild(menu);
                    return group.outerHTML;
                };
            }
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
        var columnList = controls.querySelector('[data-role="columns"]');
        var sampleList = controls.querySelector('[data-role="samples"]');
        var typeList = controls.querySelector('[data-role="types"]');
        var referenceList = controls.querySelector('[data-role="references"]');
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
            data: data,
            columns: columns,
            deferRender: true,
            autoWidth: false,
            paging: true,
            pagingType: "full_numbers",
            pageLength: pageLength,
            lengthMenu: [PAGE_LENGTHS, [50, 100, 500, 1000, "All"]],
            // The server's order, and no sort handles on the headers: a click on a sample's
            // header follows its link instead.
            ordering: false,
            // One row of controls above the table -- the pager at the left, then length and
            // the count, and the search box at the far right (the stylesheet pushes it) --
            // and the table alone in the box that scrolls. The box itself is DataTables'
            // doing, so that it wraps only the table. `B` is where Buttons makes the two
            // Export buttons; they are moved into the Export tab's pane below, so the row
            // never shows them.
            dom: '<"mutation-matrix-toolbar"plifB>r<"mutation-matrix-scroll"t>',
            // Export is a menu of two: the rows showing -- after the Show menu, the hidden
            // samples and types, and the search box, which is what `search: "applied"` means
            // -- or every row the server produced. Visible columns only, either way. Ancestral
            // rows the reader asked to see are rows the server produced, so both include them;
            // they are in no row set, so choosing one in the Show menu drops them. Cells are
            // exported through the `export` rendering, which is the displayed one except for
            // a gene list, whose whole plain list is exported rather than its Show button.
            buttons: [{
                extend: "collection",
                text: "Export CSV",
                autoClose: true,
                buttons: [{
                    extend: "csv",
                    text: "Filtered mutations",
                    title: (container.getAttribute("data-csv-title") || "mutations") + "_showing",
                    exportOptions: { columns: ":visible:not(.breseq-curate)", orthogonal: "export", modifier: { search: "applied" } }
                }, {
                    extend: "csv",
                    text: "All mutations",
                    title: container.getAttribute("data-csv-title") || "mutations",
                    exportOptions: { columns: ":visible:not(.breseq-curate)", orthogonal: "export", modifier: { search: "none" } }
                }]
            }, {
                // A vector drawing of the filtered rows, as the Display settings show them;
                // mutation_matrix_svg.js builds it from the data rather than the DOM.
                text: "Export SVG",
                action: function (e, api) {
                    var svg = window.mutationMatrixSvg(api, table);
                    var link = document.createElement("a");
                    link.href = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
                    link.download = (container.getAttribute("data-csv-title") || "mutations") + ".svg";
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    setTimeout(function () { URL.revokeObjectURL(link.href); }, 0);
                }
            }],
            language: {
                emptyTable: container.getAttribute("data-empty-message") || "No mutations to show.",
                // DataTables' own empty-count line is "Showing 0 to 0 of 0 entries", which
                // with the filtered suffix reads as three zeros in a row.
                infoEmpty: "Showing 0 entries"
            },
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

        /* Export CSV and Export SVG live in the Export tab. Buttons makes them where `B` is
           in `dom`, which is always a DataTables toolbar; their container is moved into the
           pane once they exist. */
        var exportPane = controls.querySelector('[data-role="export"]');
        if (exportPane) { dt.buttons().container().appendTo(exportPane); }

        /* The collapse bar sits directly under the panes, the sidebar's strip laid flat: a
           full-width bar with a chevron, clicked to fold away everything above it -- the
           panes, the strip, the page header -- and clicked again to bring it all back. It
           stays where it is in both states, as the sidebar's strip does, so what was folded
           is always one click from being unfolded; the chevron and `aria-expanded` say
           which state it is in. Inserted after the pane box rather than into a toolbar row,
           so it is between what it folds and the table that gets the height. Absent where
           there is nothing to fold. */
        if (optionPanes) {
            var collapseBar = document.createElement("div");
            collapseBar.className = "mutation-matrix-collapse";
            collapseBar.setAttribute("role", "button");
            collapseBar.tabIndex = 0;
            var chevron = document.createElement("i");
            chevron.setAttribute("aria-hidden", "true");
            collapseBar.appendChild(chevron);
            function drawCollapseBar() {
                chevron.className = "fa " + (optionsShown ? "fa-angle-double-up" : "fa-angle-double-down");
                collapseBar.title = optionsShown ? "Collapse everything above the table" : "Expand the header and the options";
                collapseBar.setAttribute("aria-label", collapseBar.title);
                collapseBar.setAttribute("aria-expanded", String(optionsShown));
                // Folded, the stylesheet moves it up to the top of the window.
                collapseBar.classList.toggle("folded", !optionsShown);
            }
            function toggleOptions() {
                optionsShown = !optionsShown;
                applyOptions();
                drawCollapseBar();
                prefs.set(OPTIONS_KEY, { hidden: !optionsShown });
                // The box's top moved; the ResizeObserver on the body catches it too, but
                // not where ResizeObserver is missing.
                sizeScrollBox();
            }
            drawCollapseBar();
            collapseBar.addEventListener("click", toggleOptions);
            collapseBar.addEventListener("keydown", function (event) {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleOptions(); }
            });
            optionPanes.parentNode.insertBefore(collapseBar, optionPanes.nextSibling);
        }

        /* The descriptive columns stay put while the samples scroll. `position: sticky`
           needs each pinned column's `left` to be the width of everything pinned before it,
           and widths change with the page's data, so this runs after every draw -- and again
           whenever the table's size changes, which is how a late font or a resized window
           reach it. */
        var scrollBox = container.querySelector(".mutation-matrix-scroll");
        var widths = prefs.get(WIDTHS_KEY, null) || {};  // the resized columns, below
        /* Only as many leading columns are pinned as leave room for the samples. Pinned
           columns adding up to the box's width would leave nothing that moves in view, so
           scrolling looked like nothing happening; past the room for SAMPLES_IN_VIEW
           sample columns the rest of the descriptive columns scroll with the samples, as a
           spreadsheet freezes only what fits. A descriptive column keeps `descriptive`
           (its divider) and `resized` (its clip) whether or not it is pinned. */
        var SAMPLES_IN_VIEW = 3;
        function pinColumns() {
            var left = 0, room = scrollBox ? scrollBox.clientWidth - SAMPLES_IN_VIEW * 32 : Infinity;
            var pinning = true;
            ths.forEach(function (th, i) {
                if (th.getAttribute("data-key") === null) { return; }
                var column = dt.column(i);
                var nodes = [th].concat(Array.prototype.slice.call(column.nodes()));
                if (!column.visible()) {
                    nodes.forEach(function (el) { el.classList.remove("pinned"); el.style.left = ""; });
                    return;
                }
                var resized = typeof widths[th.getAttribute("data-key")] === "number";
                var width = th.getBoundingClientRect().width;
                if (left + width > room) { pinning = false; }
                nodes.forEach(function (el) {
                    el.classList.add("descriptive");
                    el.classList.toggle("pinned", pinning);
                    el.classList.toggle("resized", resized);
                    el.style.left = pinning ? left + "px" : "";
                });
                left += width;
            });
        }

        /* The pinned columns can be resized: a handle on each one's right edge, dragged to
           set that column's width, double-clicked to let the content size it again. The
           width goes on the header cell as width, min-width and max-width at once, which
           is what makes it stick in auto table layout against the stylesheet's own floor
           and ceiling on Description; `autoWidth` is off, so DataTables writes no width of
           its own over it. Remembered per column key as `mutation_matrix.widths`. Pointer
           capture keeps the drag alive once the pointer leaves the handle. */
        function setWidth(th, value) {
            th.style.width = value; th.style.minWidth = value; th.style.maxWidth = value;
        }
        /* `px` is the column's outer width, what the handle was dragged to and what is
           stored. A table cell's `width` is its content width whatever `box-sizing` says,
           so the cell comes out wider by its padding and border: apply, measure, and take
           the difference off. */
        function applyWidth(th, px) {
            if (!px) { setWidth(th, ""); return; }
            setWidth(th, px + "px");
            var drift = th.getBoundingClientRect().width - px;
            if (drift > 0.5 && px - drift >= MIN_COLUMN_WIDTH) { setWidth(th, (px - drift) + "px"); }
        }
        ths.forEach(function (th) {
            var key = th.getAttribute("data-key");
            // The Curate column is as wide as its buttons and has nothing to resize to.
            if (key === null || key === "curate") { return; }
            if (typeof widths[key] === "number") { applyWidth(th, widths[key]); }
            var handle = document.createElement("span");
            handle.className = "mutation-matrix-resizer";
            handle.title = "Drag to resize this column; double-click to reset";
            var startX, startWidth;
            handle.addEventListener("pointerdown", function (event) {
                startX = event.clientX;
                startWidth = th.getBoundingClientRect().width;
                // Resized from this moment: the cells take the class that lets the column
                // go narrower than what they hold, before the first move asks it to.
                widths[key] = Math.round(startWidth);
                pinColumns();
                handle.setPointerCapture(event.pointerId);
                event.preventDefault();
            });
            handle.addEventListener("pointermove", function (event) {
                if (startX === undefined) { return; }
                applyWidth(th, Math.max(MIN_COLUMN_WIDTH, Math.round(startWidth + event.clientX - startX)));
                pinColumns();
            });
            handle.addEventListener("pointerup", function () {
                if (startX === undefined) { return; }
                startX = undefined;
                widths[key] = Math.round(th.getBoundingClientRect().width);
                prefs.set(WIDTHS_KEY, widths);
                pinColumns();
            });
            handle.addEventListener("dblclick", function () {
                applyWidth(th, null);
                delete widths[key];
                prefs.set(WIDTHS_KEY, widths);
                pinColumns();
            });
            th.appendChild(handle);
        });

        /* The box reaches the bottom of the window, whatever sits above it on the page, so
           its bottom scrollbar is the window's edge; its side is the table's own edge or the
           window's, whichever is nearer (the stylesheet's fit-content and its clamp). */
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
        window.addEventListener("resize", function () { sizeScrollBox(); pinColumns(); });

        /* The Curate menu opens over the scroll box rather than inside it. The box is
           `overflow: auto`, so a menu positioned in the cell would be clipped or would scroll
           the box; it is fixed to the window at its caret instead, above it when there is no
           room below. A pinned cell is its own stacking context, so the open one is raised
           over its neighbours for as long as its menu is open. Scrolling the box closes it,
           since a fixed menu would otherwise stay put while its row moved. */
        $(table).on("shown.bs.dropdown", ".mutation-matrix-curate", function () {
            var toggle = this.querySelector(".dropdown-toggle");
            var menu = this.querySelector(".dropdown-menu");
            var cell = this.closest("td");
            if (cell) { cell.classList.add("mutation-matrix-curate-open"); }
            var r = toggle.getBoundingClientRect();
            menu.style.position = "fixed";
            menu.style.left = r.left + "px";
            menu.style.top = r.bottom + 2 + "px";
            var below = window.innerHeight - r.bottom;
            if (menu.offsetHeight > below - 8 && r.top > menu.offsetHeight + 8) {
                menu.style.top = (r.top - menu.offsetHeight - 2) + "px";
            }
        });
        /* "Copy to ancestor" does it directly: the Copy tab's own endpoint, from the row's
           first carrying sample to the ancestor, at 100% -- an ancestor's mutations are fixed
           -- and recorded and restorable like any copy. The
           mutation is then ancestral -- subtracted from every sample and every set -- so the
           page reloads to show that rather than patching a table the server now describes
           differently. A refusal (a locked experiment, say) is said and changes nothing. */
        $(table).on("click", "a[data-copy-to-ancestor]", function (event) {
            event.preventDefault();
            var link = this;
            window.mutintPost(curateCopyApply, {
                experiment_id: experimentId,
                source_sample_id: link.getAttribute("data-source"),
                mutation_ids: JSON.stringify([parseInt(link.getAttribute("data-copy-to-ancestor"), 10)]),
                target_sample_ids: JSON.stringify([parseInt(curateAncestorId, 10)]),
                // Fixed in the ancestor, whatever it reached in the sample it came from.
                frequency: "1"
            }).then(function () {
                try {
                    window.sessionStorage.setItem(RETURN_KEY, JSON.stringify({
                        path: window.location.pathname + window.location.search,
                        page: dt.page(), top: scrollBox ? scrollBox.scrollTop : 0,
                        left: scrollBox ? scrollBox.scrollLeft : 0, window: window.pageYOffset
                    }));
                } catch (ignored) { /* storage refused: the reload simply starts at the top */ }
                window.location.reload();
            }).catch(function (failure) {
                window.alert("Could not copy to the ancestor: " + failure.message);
            });
        });
        $(table).on("hidden.bs.dropdown", ".mutation-matrix-curate", function () {
            var cell = this.closest("td");
            if (cell) { cell.classList.remove("mutation-matrix-curate-open"); }
        });
        if (scrollBox) {
            scrollBox.addEventListener("scroll", function () {
                $(table).find(".mutation-matrix-curate.open > .dropdown-toggle").dropdown("toggle");
            });
        }
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

        var counter = controls.querySelector('[data-role="sample-count"]');
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
                prefs.set(COLUMNS_KEY, window.mutintPreferences.columnChoice(hiddenColumns, columnDefaults));
            }
        });
        var typeCounter = controls.querySelector('[data-role="type-count"]');
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
        Array.prototype.forEach.call(controls.querySelectorAll("[data-types]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-types") === "all";
                typePicker.select(function () { return all; });
            });
        });
        var referenceCounter = controls.querySelector('[data-role="reference-count"]');
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
        Array.prototype.forEach.call(controls.querySelectorAll("[data-references]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-references") === "all";
                referencePicker.select(function () { return all; });
            });
        });
        /* Steps 1, 3 and 4 of a change (step 2 when `dataChanged`): which columns show, what
           the row sets hold, and one draw. Every control below ends here. */
        var setNotes = [];
        function refresh(dataChanged) {
            shownSampleIndexes = {};
            sampleMeta.forEach(function (meta) {
                var on = isShown(meta);
                if (on) { shownSampleIndexes[meta.index] = true; }
                dt.column(meta.column).visible(on, false);
                var li = sampleList.querySelector('li[data-value="' + meta.id + '"]');
                if (li) {
                    var reason = hiddenBy(meta);
                    li.classList.toggle("mutint-select-excluded", !!reason);
                    li.title = reason ? "Hidden by the " + reason : "";
                }
            });
            if (dataChanged) {
                data = buildData();
                dt.clear();
                dt.rows.add(data);
            }
            computeSets();
            if (counter) { counter.textContent = samplePicker.count(); }
            writeSummary();
            dt.columns.adjust().draw();
            pinColumns();
        }

        var samplePicker = window.mutintSelectList(sampleList, {
            toggle: true, controls: null,
            onChange: function (changed) {
                changed.forEach(function (li) {
                    var id = li.getAttribute("data-value");
                    if (samplePicker.isSelected(li)) { delete hiddenSamples[id]; } else { hiddenSamples[id] = true; }
                });
                refresh(false);
                if (samplesKey) {
                    prefs.set(samplesKey, { hidden: keys(hiddenSamples).map(Number) });
                }
            }
        });
        if (counter) { counter.textContent = samplePicker.count(); }

        Array.prototype.forEach.call(controls.querySelectorAll("[data-samples]"), function (button) {
            button.addEventListener("click", function () {
                var all = button.getAttribute("data-samples") === "all";
                samplePicker.select(function () { return all; });
            });
        });

        /* The layers: Populations, Treatments and Sample types are the Samples menu again,
           over what samples share, each remembered as its own hidden set. Absent menus --
           Search has none, an experiment with no treatment has no Treatments tab -- are
           skipped. */
        function layerPicker(role, hidden, key, buttonAttr, countRole) {
            var list = controls.querySelector('[data-role="' + role + '"]');
            if (!list) { return null; }
            Array.prototype.forEach.call(list.querySelectorAll("li[data-value]"), function (li) {
                li.classList.toggle("active", !hidden[li.getAttribute("data-value")]);
            });
            var count = controls.querySelector('[data-role="' + countRole + '"]');
            var picker = window.mutintSelectList(list, {
                toggle: true, controls: null,
                onChange: function (changed) {
                    changed.forEach(function (li) {
                        var value = li.getAttribute("data-value");
                        if (picker.isSelected(li)) { delete hidden[value]; } else { hidden[value] = true; }
                    });
                    if (count) { count.textContent = picker.count(); }
                    if (key) { prefs.set(key, { hidden: keys(hidden) }); }
                    refresh(false);
                }
            });
            if (count) { count.textContent = picker.count(); }
            Array.prototype.forEach.call(controls.querySelectorAll("[" + buttonAttr + "]"), function (button) {
                button.addEventListener("click", function () {
                    var all = button.getAttribute(buttonAttr) === "all";
                    picker.select(function () { return all; });
                });
            });
            return picker;
        }
        var populationPicker = layerPicker("populations", hiddenPopulations, populationsKey,
                                           "data-populations", "population-count");
        var treatmentPicker = layerPicker("treatments", hiddenTreatments, treatmentsKey,
                                          "data-treatments", "treatment-count");
        var sampleTypePicker = layerPicker("sample-types", hiddenSampleTypes, sampleTypesKey,
                                           "data-sample-types", "sample-type-count");

        /* The Time Points tab: two range inputs over the index of the time points some sample was
           taken at, so the stops are evenly spaced, and two boxes holding the range itself,
           which take any number. A handle moved sets its box to that stop; a number typed
           sets the bound and puts its handle on the nearest stop inside it. The table follows
           when a handle is let go, not on every pixel of the drag. */
        var timeBox = controls.querySelector('[data-role="time"]');
        if (timeBox) {
            var times = timeBox.getAttribute("data-times").split(",").map(parseFloat);
            var last = times.length - 1;
            var low = timeBox.querySelector('[data-role="time-low"]');
            var high = timeBox.querySelector('[data-role="time-high"]');
            var minBox = timeBox.querySelector('[data-role="time-min"]');
            var maxBox = timeBox.querySelector('[data-role="time-max"]');
            var fill = timeBox.querySelector('[data-role="time-fill"]');
            var timeCount = timeBox.querySelector('[data-role="time-count"]');
            var firstAtLeast = function (value) {
                for (var i = 0; i <= last; i++) { if (times[i] >= value) { return i; } }
                return last;
            };
            var lastAtMost = function (value) {
                for (var i = last; i >= 0; i--) { if (times[i] <= value) { return i; } }
                return 0;
            };
            var drawTime = function (lowIndex, highIndex) {
                if (lowIndex === undefined) {
                    lowIndex = timeRange.min === null ? 0 : firstAtLeast(timeRange.min);
                    highIndex = timeRange.max === null ? last : lastAtMost(timeRange.max);
                }
                low.value = lowIndex;
                high.value = Math.max(lowIndex, highIndex);
                // The upper input is on top, so with both handles at the far end its thumb
                // would cover the lower one, which could then never move left.
                low.style.zIndex = lowIndex === last ? "2" : "";
                minBox.value = String(timeRange.min === null ? times[0] : timeRange.min);
                maxBox.value = String(timeRange.max === null ? times[last] : timeRange.max);
                fill.style.left = (100 * lowIndex / last) + "%";
                fill.style.right = (100 * (last - Math.max(lowIndex, highIndex)) / last) + "%";
                var inRange = times.filter(function (t) {
                    return (timeRange.min === null || t >= timeRange.min) && (timeRange.max === null || t <= timeRange.max);
                }).length;
                timeCount.textContent = inRange + " of " + times.length + " time points";
            };
            var saveTime = function () {
                if (timeKey) { prefs.set(timeKey, { min: timeRange.min, max: timeRange.max }); }
                drawTime();
                refresh(false);
            };
            /* A handle at either end is no bound at all, so a time point added later is in. */
            var fromHandles = function () {
                var lowIndex = Math.min(parseInt(low.value, 10), parseInt(high.value, 10));
                var highIndex = Math.max(parseInt(low.value, 10), parseInt(high.value, 10));
                timeRange.min = lowIndex === 0 ? null : times[lowIndex];
                timeRange.max = highIndex === last ? null : times[highIndex];
                return [lowIndex, highIndex];
            };
            [low, high].forEach(function (handle) {
                handle.addEventListener("input", function () {
                    // Neither handle passes the other.
                    if (handle === low && parseInt(low.value, 10) > parseInt(high.value, 10)) { low.value = high.value; }
                    if (handle === high && parseInt(high.value, 10) < parseInt(low.value, 10)) { high.value = low.value; }
                    var span = fromHandles();
                    drawTime(span[0], span[1]);
                });
                handle.addEventListener("change", function () { fromHandles(); saveTime(); });
            });
            var typed = function (box, isMin) {
                var text = box.value.trim(), value = text === "" ? null : Number(text);
                if (value !== null && !isFinite(value)) { drawTime(); return; }
                if (isMin) {
                    if (value !== null && timeRange.max !== null && value > timeRange.max) { value = timeRange.max; }
                    timeRange.min = value === null || value <= times[0] ? null : value;
                } else {
                    if (value !== null && timeRange.min !== null && value < timeRange.min) { value = timeRange.min; }
                    timeRange.max = value === null || value >= times[last] ? null : value;
                }
                saveTime();
            };
            [[minBox, true], [maxBox, false]].forEach(function (pair) {
                pair[0].addEventListener("change", function () { typed(pair[0], pair[1]); });
                pair[0].addEventListener("keydown", function (event) {
                    if (event.key === "Enter") { event.preventDefault(); typed(pair[0], pair[1]); }
                });
            });
            timeBox.querySelector('[data-role="time-reset"]').addEventListener("click", function () {
                timeRange.min = null;
                timeRange.max = null;
                saveTime();
            });
            drawTime();
        }

        /* Step 3: the row sets the page registered, over the shown samples and the kept cells
           of the rows a set may hold. Parameters come from the page's `[data-set-param]`
           inputs, remembered per experiment. */
        var setParams = setParamsKey ? (prefs.get(setParamsKey, null) || {}) : {};
        var paramInputs = Array.prototype.slice.call(controls.querySelectorAll("[data-set-param]"));
        paramInputs.forEach(function (input) {
            var name = input.getAttribute("data-set-param");
            if (typeof setParams[name] === "string") { input.value = setParams[name]; }
            var changed = function () {
                setParams[name] = input.value;
                if (setParamsKey) { prefs.set(setParamsKey, setParams); }
                refresh(false);
            };
            input.addEventListener("change", changed);
            input.addEventListener("keydown", function (event) {
                if (event.key === "Enter") { event.preventDefault(); changed(); }
            });
        });
        function currentParams() {
            var out = {};
            paramInputs.forEach(function (input) { out[input.getAttribute("data-set-param")] = input.value; });
            return out;
        }
        function computeSets() {
            var allCount = controls.querySelector('[data-role="all-count"]');
            if (allCount) { allCount.textContent = data.length; }
            setNotes = [];
            var items = showList ? showList.querySelectorAll("li[data-client-set]") : [];
            if (!items.length) { return; }
            var state = {
                samples: sampleMeta.filter(function (meta) { return shownSampleIndexes[meta.index]; })
                    .map(function (meta) { return { index: meta.index, population: meta.population, time: meta.time }; }),
                rows: data.filter(function (row) { return !row.ancestral; })
                    .map(function (row) { return { id: row.id, genes: row.genes || [], cells: row.samples }; })
            };
            var params = currentParams(), membership = {};
            Array.prototype.forEach.call(items, function (li) {
                var key = li.getAttribute("data-value"), fn = window.mutintMatrixSets.get(key);
                var result = fn ? fn(state, params) : { ids: [], note: "" };
                (result.ids || []).forEach(function (id) { (membership[id] = membership[id] || []).push(key); });
                var count = li.querySelector('[data-role="set-count"]');
                if (count) { count.textContent = (result.ids || []).length; }
                setNotes.push(result);
            });
            data.forEach(function (row) { row.sets = row._sets.concat(membership[row.id] || []); });
        }

        /* What is hidden and why, each beside its controls: the reader's frequency range on
           the Frequencies tab (in the server's own words,
           mutint_filter's _summary.html), and each row set's sentence on the Sets tab. Built
           from nodes, since names are the reader's text. A target the page does not have is
           skipped. */
        var filterSummary = controls.querySelector('[data-role="filter-summary"]');
        var setNotesBox = controls.querySelector('[data-role="set-notes"]');
        var ancestralToggle = controls.querySelector('[data-role="ancestral-toggle"]');
        function line(parent, text) {
            var div = document.createElement("div");
            if (text) { div.textContent = text; }
            parent.appendChild(div);
            return div;
        }
        function writeSummary() {
            if (ancestralToggle) {
                ancestralToggle.textContent = ancestralShown ? "Hide ancestral mutations"
                                                             : "Show ancestral mutations";
                ancestralToggle.classList.toggle("active", ancestralShown);
            }
            if (filterSummary) {
                filterSummary.textContent = "";
                var cutoff = viewFilter.min_freq !== null || viewFilter.max_freq !== null;
                var genes = applyGenes ? (viewFilter.genes || []) : [];
                if (!cutoff && !genes.length) {
                    line(filterSummary, "Every frequency is shown.");
                } else {
                    var parts = [];
                    if (cutoff) {
                        parts.push("frequencies " + (viewFilter.min_freq || 0) + "\u2013" +
                                   (viewFilter.max_freq === null ? 100 : viewFilter.max_freq) + "%");
                    }
                    if (genes.length) {
                        parts.push("mutations outside " + genes.length + " ignored gene" +
                                   (genes.length === 1 ? "" : "s") + " (" + genes.join(", ") + ")");
                    }
                    line(filterSummary, "Showing only " + parts.join(", and ") +
                                        ". This is your own view; nobody else's page is affected.");
                }
            }
            if (setNotesBox) {
                setNotesBox.textContent = "";
                setNotes.forEach(function (result) {
                    if (result.note) { line(setNotesBox, result.note); }
                    if (result.error) { line(setNotesBox, result.error).className = "text-danger"; }
                });
            }
        }

        /* The reader's filter: applied here, and written back to the session -- the one
           filter every other page reads -- through `/filter/set`, which answers with the value
           normalized the server's way; that answer is what is applied. A value it refuses
           is said under the boxes, and nothing changes. */
        if (filterBox) {
            var minInput = filterBox.querySelector('[data-role="filter-min"]');
            var maxInput = filterBox.querySelector('[data-role="filter-max"]');
            var genesInput = controls.querySelector('[data-role="filter-genes"]');
            var filterError = controls.querySelector('[data-role="filter-error"]');
            var filterUrl = filterBox.getAttribute("data-url");
            var sendState = function (payload) {
                payload.experiment_id = parseInt(experimentId, 10);
                return window.mutintPostJson(filterUrl, payload);
            };
            var showFilter = function () {
                minInput.value = viewFilter.min_freq === null ? "" : viewFilter.min_freq;
                maxInput.value = viewFilter.max_freq === null ? "" : viewFilter.max_freq;
                if (genesInput) { genesInput.value = (viewFilter.genes || []).join(", "); }
            };
            var submitFilter = function () {
                sendState({ filter: { min_freq: minInput.value, max_freq: maxInput.value,
                                      // Without its box, the session's list goes back as
                                      // it was, for the pages that do offer it.
                                      genes: genesInput ? genesInput.value
                                                        : (viewFilter.genes || []).join(", ") } })
                    .then(function (body) {
                        viewFilter = body.filter;
                        filterError.hidden = true;
                        showFilter();
                        refresh(true);
                    })
                    .catch(function (failure) {
                        filterError.textContent = failure.message;
                        filterError.hidden = false;
                    });
            };
            [minInput, maxInput, genesInput].filter(Boolean).forEach(function (input) {
                input.addEventListener("change", submitFilter);
                input.addEventListener("keydown", function (event) {
                    if (event.key === "Enter") { event.preventDefault(); submitFilter(); }
                });
            });
            filterBox.querySelector('[data-role="filter-clear"]').addEventListener("click", function () {
                minInput.value = "";
                maxInput.value = "";
                if (genesInput) { genesInput.value = ""; }
                submitFilter();
            });
            if (ancestralToggle) {
                ancestralToggle.addEventListener("click", function () {
                    ancestralShown = !ancestralShown;
                    refresh(true);
                    sendState({ ancestral: ancestralShown }).catch(function (failure) {
                        filterError.textContent = failure.message;
                        filterError.hidden = false;
                    });
                });
            }
        }

        /* The Frequency display menu, in the Frequencies tab. Choosing a format swaps one class
           on the table. */
        var frequencyList = controls.querySelector('[data-role="frequency"]');
        var frequencyLabel = controls.querySelector('[data-role="frequency-label"]');
        var legend = controls.querySelector('[data-role="frequency-legend"]');
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
        var showLabel = controls.querySelector('[data-role="show-label"]');
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

        /* The two "Color sample columns by" boxes, on the Populations and Treatments tabs, are
           one choice: population (the default), treatment, or none. Ticking one unticks the
           other, unticking the ticked one leaves the columns uncolored. It is one class on
           the table; the stylesheet does the rest, header and bars alike, and the SVG export
           reads the colors back from it. A remembered "treatment" on an experiment that no
           longer has a Treatments tab reads as population. */
        var colorBoxes = Array.prototype.slice.call(controls.querySelectorAll("[data-color]"));
        if (colorBoxes.length) {
            var colorKey = perExperiment(COLOR_KEY_PREFIX);
            var storedColor = colorKey ? prefs.get(colorKey, null) : null;
            var colorMode = storedColor && COLOR_MODES[storedColor.by] ? storedColor.by : "population";
            if (colorMode === "treatment" && !controls.querySelector('[data-color="treatment"]')) {
                colorMode = "population";
            }
            var showColor = function () {
                Object.keys(COLOR_MODES).forEach(function (mode) {
                    table.classList.toggle("color-" + mode, mode === colorMode);
                });
                colorBoxes.forEach(function (box) { box.checked = box.getAttribute("data-color") === colorMode; });
            };
            showColor();
            colorBoxes.forEach(function (box) {
                box.addEventListener("change", function () {
                    colorMode = box.checked ? box.getAttribute("data-color") : "none";
                    showColor();
                    if (colorKey) { prefs.set(colorKey, { by: colorMode }); }
                });
            });
        }

        /* Cell padding, in the Columns tab: Normal is the Mutations page's cell padding,
           Condensed one line per row. One class on the table, and the pinned offsets
           recomputed, since the descriptive columns' widths move with their padding. */
        function showView(name) {
            Object.keys(VIEWS).forEach(function (key) { table.classList.toggle("view-" + key, key === name); });
            Array.prototype.forEach.call(controls.querySelectorAll("[data-view]"), function (button) {
                button.classList.toggle("active", button.getAttribute("data-view") === name);
            });
            pinColumns();
        }
        showView(view);
        Array.prototype.forEach.call(controls.querySelectorAll("[data-view]"), function (button) {
            button.addEventListener("click", function () {
                view = button.getAttribute("data-view");
                showView(view);
                prefs.set(VIEW_KEY, { view: view });
            });
        });

        // The first refresh: the sets need the table, and the summary needs the sets.
        if (decidesHere) { refresh(false); }

        dt.on("length.dt", function (event, settings, length) {
            prefs.set(PAGE_LENGTH_KEY, { length: length });
        });

        // Back where the reader was, if this load is the one Copy to ancestor made.
        var returning = null;
        try {
            returning = JSON.parse(window.sessionStorage.getItem(RETURN_KEY) || "null");
            window.sessionStorage.removeItem(RETURN_KEY);
        } catch (ignored) { returning = null; }
        if (returning && returning.path === window.location.pathname + window.location.search) {
            if (returning.page > 0 && returning.page < dt.page.info().pages) { dt.page(returning.page).draw(false); }
            if (scrollBox) { scrollBox.scrollTop = returning.top || 0; scrollBox.scrollLeft = returning.left || 0; }
            window.scrollTo(0, returning.window || 0);
        }

        // For a harness or a console: the DataTable behind the container.
        container.mutationMatrix = { table: dt, columns: columnPicker, samples: samplePicker, types: typePicker, references: referencePicker, frequency: frequencyPicker, show: showPicker,
                                     populations: populationPicker, treatments: treatmentPicker, sampleTypes: sampleTypePicker };
    }

    $(function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-mutation-matrix]"), init);
    });
}());

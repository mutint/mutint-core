/* The mutation matrix's Export tab: the file, from the table's data.
 *
 * Three choices and a button. Rows: the rows showing -- after every menu and the Filter
 * tab's rules, DataTables' `search: "applied"` -- or every row the table holds. Columns: the
 * ones showing -- after the Columns menu and the sample layers -- or every column and every
 * sample, hidden or not. Format: CSV, a standalone HTML table styled as this page styles it,
 * or the SVG drawing mutation_matrix_svg.js makes. The Curate column is never exported: it
 * is buttons, not data.
 *
 * Built from DataTables' data rather than the DOM, as the SVG is: the DOM holds only the page
 * on screen (`deferRender`), and a hidden column's cells are not in it at all.
 *
 * `collect(dt, options)` interprets the two choices once, for every format. `csv` and `html`
 * are pure functions of what it returns and run under node (mutint_common/tests/
 * test_matrix_export_js.py); `collect`, `styles` and `download` need a browser.
 *
 * **The HTML carries the matrix's own stylesheet**, read back from the document
 * (`document.styleSheets`, the sheet whose href is breseq_table.css) rather than copied into
 * this file -- so the colours, the frequency bars and the heat map in the exported table are
 * whatever the page draws, with nothing to keep in step by hand. Links are dropped: they
 * point into this server, and the file is for somewhere else.
 */
(function () {
    "use strict";

    var BOM = "﻿";
    var MIME = { csv: "text/csv;charset=utf-8", html: "text/html;charset=utf-8",
                 svg: "image/svg+xml" };

    /* What a number cell shows: `42` for 42.0%, the check mark as it is. The CSV has always
       carried the bare number a cell shows; mutation_matrix.js and the SVG agree. */
    function compact(cell) {
        if (typeof cell.s !== "number" || cell.f.indexOf("%") < 0) { return cell.f; }
        return String(Math.round(cell.s * 100));
    }

    function escape(text) {
        return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    /* A tag-stripper that needs no DOM, so the CSV can be made under node too: buttons go with
       their text (a collapsed list's Show button is not a gene), every other tag goes and its
       text stays -- the hidden half of a collapsed list included, because the CSV wants the
       whole list, which is also what `<key>_text` carries where the server wrote one. */
    function stripTags(html) {
        if (html === null || html === undefined) { return ""; }
        var text = String(html).replace(/<button\b[\s\S]*?<\/button>/gi, "")
            .replace(/<[^>]+>/g, "")
            .replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
            .replace(/&quot;/g, '"').replace(/&#0*39;/g, "'").replace(/&amp;/g, "&");
        return text.replace(/\s+/g, " ").trim();
    }

    /* A cell's markup for the HTML file: buttons out, `hidden` lifted so a collapsed list
       opens (there is no script in the file to open it), every link unwrapped to its text. */
    function exportMarkup(html) {
        if (html === null || html === undefined) { return ""; }
        return String(html).replace(/<button\b[\s\S]*?<\/button>/gi, "")
            .replace(/<([a-z][^>]*?)\s+hidden(?=[\s>])/gi, "<$1")
            .replace(/<a\b[^>]*>/gi, "").replace(/<\/a>/gi, "");
    }

    function cellText(row, column) {
        if (column.kind === "sample") {
            var cell = row.samples && row.samples[column.index];
            return cell ? compact(cell) : "";
        }
        var twin = row[column.key + "_text"];
        return typeof twin === "string" ? twin : stripTags(row[column.key]);
    }

    /* The sample cell as mutation_matrix.js renders it, as a <span> -- class, `--f` and title
       kept, so the stylesheet draws the bar or the heat map from the same markup. */
    function sampleMarkup(cell) {
        if (!cell) { return ""; }
        var f = typeof cell.s === "number" ? String(cell.s) : "1";
        return '<span class="mutation-matrix-cell' + (cell.p ? " polymorphic" : "") +
            '" style="--f: ' + f + '" title="' + escape(cell.f) + '"><span>' +
            escape(compact(cell)) + "</span></span>";
    }

    /* The table's data, as the two choices leave it. Headers come from DataTables rather than
       the DOM: a hidden column's <th> is out of the document. */
    function collect(dt, options) {
        options = options || {};
        var rows = dt.rows({ search: options.rows === "all" ? "none" : "applied" }).data().toArray();
        var columns = [];
        dt.columns().indexes().toArray().forEach(function (i) {
            if (options.columns !== "all" && !dt.column(i).visible()) { return; }
            var th = dt.column(i).header();
            var key = th.getAttribute("data-key");
            if (key === "curate") { return; }
            if (key !== null) {
                columns.push({ kind: "text", key: key, title: th.textContent.trim(),
                               className: th.className });
                return;
            }
            var copy = th.cloneNode(true);
            Array.prototype.forEach.call(copy.querySelectorAll(".sample-flag"), function (el) { el.remove(); });
            columns.push({ kind: "sample", index: parseInt(th.getAttribute("data-index"), 10),
                           title: copy.textContent.replace(/\s+/g, " ").trim(),
                           className: th.className });
        });
        return { columns: columns, rows: rows };
    }

    function csvField(text) {
        text = String(text);
        return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }

    function csv(collected) {
        var lines = [collected.columns.map(function (c) { return csvField(c.title); }).join(",")];
        collected.rows.forEach(function (row) {
            lines.push(collected.columns.map(function (c) { return csvField(cellText(row, c)); }).join(","));
        });
        return BOM + lines.join("\r\n") + "\r\n";
    }

    function currentClass(table, prefix, fallback) {
        for (var i = 0; i < table.classList.length; i++) {
            if (table.classList[i].indexOf(prefix) === 0) {
                return table.classList[i].slice(prefix.length);
            }
        }
        return fallback;
    }

    /* The matrix's stylesheet, read back from the page. Same origin, so its rules are
       readable; a sheet that is not (or a document with none) contributes nothing, and the
       file is still a table. */
    function styles(doc) {
        var out = [];
        if (!doc || !doc.styleSheets) { return ""; }
        Array.prototype.forEach.call(doc.styleSheets, function (sheet) {
            if (!sheet.href || !/\/breseq_table\.css(\?|$)/.test(sheet.href)) { return; }
            try {
                Array.prototype.forEach.call(sheet.cssRules, function (rule) { out.push(rule.cssText); });
            } catch (error) { /* cross-origin: nothing to copy */ }
        });
        return out.join("\n");
    }

    /* A standalone document of the table as the page draws it: its classes (frequency
       display, cell padding, colouring), the headers in their colours, the stripes. */
    function html(collected, options) {
        options = options || {};
        var classes = ["breseq-table", "mutation-matrix-table", "dataTable",
                       "freq-" + (options.format || "number"), "view-" + (options.view || "condensed"),
                       "color-" + (options.color || "population")];
        var out = [];
        out.push("<!doctype html>");
        out.push('<html><head><meta charset="utf-8">');
        out.push("<title>" + escape(options.title || "mutations") + "</title>");
        out.push("<style>");
        out.push("body { font-family: Helvetica, Arial, sans-serif; font-size: 14px; margin: 1em; }");
        out.push("table { border-collapse: collapse; }");
        out.push("th, td { padding: 1px 4px; vertical-align: middle; }");
        out.push(options.styles || "");
        out.push("</style></head><body>");
        out.push('<table class="' + classes.join(" ") + '"><thead><tr>');
        collected.columns.forEach(function (c) {
            var inner = c.kind === "sample"
                ? '<span class="mutation-matrix-vertical">' + escape(c.title) + "</span>"
                : escape(c.title);
            out.push('<th class="' + escape(c.className) + '">' + inner + "</th>");
        });
        out.push("</tr></thead><tbody>");
        collected.rows.forEach(function (row, i) {
            var rowClass = "alternate_table_row_" + (i % 2) + (row.ancestral ? " ancestral_table_row" : "");
            out.push('<tr class="' + rowClass + '">');
            collected.columns.forEach(function (c) {
                var inner = c.kind === "sample"
                    ? sampleMarkup(row.samples && row.samples[c.index])
                    : exportMarkup(row[c.key]);
                out.push('<td class="' + escape(c.className) + '">' + inner + "</td>");
            });
            out.push("</tr>");
        });
        out.push("</tbody></table></body></html>");
        return out.join("\n");
    }

    function download(text, filename, mime) {
        var link = document.createElement("a");
        link.href = URL.createObjectURL(new Blob([text], { type: mime }));
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(link.href); }, 0);
    }

    /* The button: collect, format, download. The filename is the page's (`data-csv-title`),
       with `_showing` when only the filtered rows are in it, as the CSV was always named. */
    function run(dt, table, container, options) {
        var stem = (container.getAttribute("data-csv-title") || "mutations") +
            (options.rows === "all" ? "" : "_showing");
        var text;
        if (options.format === "svg") {
            text = window.mutationMatrixSvg(dt, table, options);
        } else {
            var collected = collect(dt, options);
            text = options.format === "html"
                ? html(collected, { title: stem, styles: styles(document),
                                    format: currentClass(table, "freq-", "number"),
                                    view: currentClass(table, "view-", "condensed"),
                                    color: currentClass(table, "color-", "population") })
                : csv(collected);
        }
        download(text, stem + "." + options.format, MIME[options.format]);
    }

    var api = { collect: collect, cellText: cellText, csv: csv, html: html, styles: styles,
                download: download, run: run, stripTags: stripTags, exportMarkup: exportMarkup };
    if (typeof window !== "undefined") { window.mutationMatrixExport = api; }
    if (typeof module !== "undefined" && module.exports) { module.exports = api; }
}());

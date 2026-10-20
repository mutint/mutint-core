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
 * **The HTML carries its formatting inline, cell by cell, because Excel reads nothing else.**
 * Excel's HTML importer understands inline `style`, `<i>`, simple colours and its own
 * `mso-rotate`, and none of what the page draws with: custom properties (`--sample-color`,
 * `--f`), `::before` (the bars), `color-mix()` (the heat map), `writing-mode`. So every th and
 * td carries its resolved background, colour, weight and alignment, and a frequency cell is
 * a number on a shaded cell -- the heat colour, or the population colour tinted by frequency
 * where the page shows bars, which a spreadsheet cannot draw. The same file in every viewer;
 * the SVG is the figure. The colours and mixes are the SVG module's, so the two cannot
 * disagree. The matrix's own stylesheet is read back from the document
 * (`document.styleSheets`, the sheet whose href is breseq_table.css) and carried too, for the
 * fonts, padding and vertical sample names a browser draws from it. Links are dropped: they
 * point into this server, and the file is for somewhere else.
 */
(function () {
    "use strict";

    var BOM = "﻿";
    // The SVG module, for its colours: on the page it is a global; under node, a sibling.
    var svg = (typeof window !== "undefined" && window.mutationMatrixSvg) ||
        (typeof require === "function" ? require("./mutation_matrix_svg.js") : null);
    var INK = "#333333";
    var DEFAULT_SAMPLE_COLOR = "#337ab7";
    // breseq_table.css's column alignment, repeated so Excel reads it: the stylesheet's
    // rules are by class and Excel reads a cell's own style.
    var ALIGN = { "breseq-position": "right", "breseq-freq": "right", "breseq-description": "left" };
    // The stylesheet's .snp_type_* colours, repeated for the same reason.
    var SNP_COLORS = { snp_type_synonymous: "green", snp_type_nonsynonymous: "blue",
                       snp_type_nonsense: "red" };
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
       opens (there is no script in the file to open it), every link unwrapped to its text,
       and a SNP type's colour written inline where its class carried it. */
    function exportMarkup(html) {
        if (html === null || html === undefined) { return ""; }
        return String(html).replace(/<button\b[\s\S]*?<\/button>/gi, "")
            .replace(/<([a-z][^>]*?)\s+hidden(?=[\s>])/gi, "<$1")
            .replace(/<a\b[^>]*>/gi, "").replace(/<\/a>/gi, "")
            .replace(/<span class="(snp_type_[a-z]+)">/g, function (tag, cls) {
                return SNP_COLORS[cls] ? '<span class="' + cls + '" style="color: ' + SNP_COLORS[cls] + '">' : tag;
            });
    }

    function cellText(row, column) {
        if (column.kind === "sample") {
            var cell = row.samples && row.samples[column.index];
            return cell ? compact(cell) : "";
        }
        var twin = row[column.key + "_text"];
        return typeof twin === "string" ? twin : stripTags(row[column.key]);
    }

    /* A frequency cell's shade and ink for the HTML file, by the page's Frequency display:
       the row's ground for Number; the population colour tinted by frequency for Bars (a
       spreadsheet cannot draw a bar; a polymorphic call takes the bar's half tone); the heat
       colour for the two heat formats, with white digits once the cell is dark, the
       stylesheet's own rule. An absent cell keeps the ground, so absence and 0% differ. */
    function sampleShade(cell, column, format, ground) {
        if (!cell) { return { background: ground, ink: INK }; }
        var f = typeof cell.s === "number" ? cell.s : 1;
        if (format === "bars") {
            return { background: svg.mixSrgb("#ffffff", column.color || DEFAULT_SAMPLE_COLOR, cell.p ? f / 2 : f), ink: INK };
        }
        if (format === "heat" || format === "both") {
            return { background: svg.heatColor(f), ink: f > 0.55 ? "#ffffff" : INK };
        }
        return { background: ground, ink: INK };
    }

    /* A computed colour as hex; `rgb(51, 122, 183)` is what getComputedStyle answers. */
    function cssHex(value, fallback) {
        var match = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(value || "");
        if (!match) { return /^#[0-9a-f]{6}$/i.test((value || "").trim()) ? value.trim() : fallback; }
        return "#" + match.slice(1, 4).map(function (n) {
            var v = parseInt(n, 10);
            return (v < 16 ? "0" : "") + v.toString(16);
        }).join("");
    }
    function sampleColor(th) {
        var value = getComputedStyle(th).getPropertyValue("--sample-color").trim();
        if (/^#[0-9a-f]{6}$/i.test(value)) { return value; }
        return cssHex(getComputedStyle(th).backgroundColor, DEFAULT_SAMPLE_COLOR);
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
                           className: th.className, color: sampleColor(th) });
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

    function alignOf(column) {
        if (column.kind === "sample") { return "center"; }
        var classes = (column.className || "").split(/\s+/);
        for (var i = 0; i < classes.length; i++) { if (ALIGN[classes[i]]) { return ALIGN[classes[i]]; } }
        return "center";
    }

    function style(pairs) {
        return ' style="' + Object.keys(pairs).filter(function (k) { return pairs[k]; })
            .map(function (k) { return k + ": " + pairs[k]; }).join("; ") + '"';
    }

    /* A standalone document of the table as the page draws it, every cell styled inline
       (see the header: Excel reads nothing else), the table wearing the page's padding and
       colouring classes for the stylesheet carried beside it. */
    function html(collected, options) {
        options = options || {};
        var colors = svg.colors;
        var classes = ["breseq-table", "mutation-matrix-table", "dataTable",
                       "view-" + (options.view || "condensed"), "color-" + (options.color || "population")];
        var out = [];
        out.push("<!doctype html>");
        out.push('<html><head><meta charset="utf-8">');
        out.push("<title>" + escape(options.title || "mutations") + "</title>");
        out.push("<style>");
        out.push("body { font-family: Helvetica, Arial, sans-serif; font-size: 14px; margin: 1em; }");
        out.push("table { border-collapse: collapse; }");
        out.push("th, td { padding: 1px 4px; vertical-align: middle; }");
        out.push(options.styles || "");
        // The page's rule keys on the `--f` span this file does not write.
        out.push("td.breseq-sample { font-size: 9pt; text-align: center; }");
        out.push("</style></head><body>");
        out.push('<table class="' + classes.join(" ") + '"><thead><tr>');
        collected.columns.forEach(function (c) {
            var inner = c.kind === "sample"
                ? '<span class="mutation-matrix-vertical">' + escape(c.title) + "</span>"
                : escape(c.title);
            var th = { "background-color": c.kind === "sample" ? (c.color || DEFAULT_SAMPLE_COLOR) : colors.header,
                       "color": "#ffffff", "font-weight": "bold", "text-align": alignOf(c) };
            if (c.kind === "sample") { th["vertical-align"] = "bottom"; th["mso-rotate"] = "90"; }
            out.push('<th class="' + escape(c.className) + '"' + style(th) + ">" + inner + "</th>");
        });
        out.push("</tr></thead><tbody>");
        collected.rows.forEach(function (row, i) {
            var rowClass = "alternate_table_row_" + (i % 2) + (row.ancestral ? " ancestral_table_row" : "");
            var ground = row.ancestral ? colors.ancestral : (i % 2 ? colors.stripe : "#ffffff");
            out.push('<tr class="' + rowClass + '">');
            collected.columns.forEach(function (c) {
                var inner, td;
                if (c.kind === "sample") {
                    var cell = row.samples && row.samples[c.index];
                    var shade = sampleShade(cell, c, options.format, ground);
                    td = { "background-color": shade.background, "color": shade.ink, "text-align": "center" };
                    out.push('<td class="' + escape(c.className) + '"' + style(td) +
                             (cell ? ' title="' + escape(cell.f) + '"' : "") + ">" +
                             (cell ? escape(compact(cell)) : "") + "</td>");
                    return;
                }
                inner = exportMarkup(row[c.key]);
                td = { "background-color": ground, "text-align": alignOf(c) };
                out.push('<td class="' + escape(c.className) + '"' + style(td) + ">" + inner + "</td>");
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
                download: download, run: run, stripTags: stripTags, exportMarkup: exportMarkup,
                sampleShade: sampleShade };
    if (typeof window !== "undefined") { window.mutationMatrixExport = api; }
    if (typeof module !== "undefined" && module.exports) { module.exports = api; }
}());

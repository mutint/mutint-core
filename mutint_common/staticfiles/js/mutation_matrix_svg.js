/* The mutation matrix as a vector drawing: Export SVG.
 *
 * Built from the table's data rather than copied from its DOM, for two reasons: the DOM
 * holds only the page on screen (`deferRender`), and the export is every row the menus and
 * the search box leave -- the rows "Filtered mutations" writes to CSV -- across every page;
 * and an HTML table wrapped in <foreignObject> is not a vector drawing to anything that
 * edits figures. So this writes plain <rect>, <text> and <line> in sRGB hex, which
 * Illustrator, Inkscape and browsers all read the same way.
 *
 * It follows what the reader chose: the visible columns and samples in table order, the
 * Frequency display format and the Normal / Condensed view. Colors come from the page --
 * each sample header's `--sample-color` is read back from the stylesheet, so the CSS stays
 * the one palette -- except the heat map, whose CSS is a `color-mix(in oklab ...)` no SVG
 * reader understands; it is mixed here in oklab the same way, so the file matches the screen.
 *
 * `window.mutationMatrixSvg(dt, table)` returns the SVG as a string; mutation_matrix.js
 * owns the button and the download.
 */
(function () {
    "use strict";

    var FONT = "Helvetica, Arial, sans-serif";
    var BODY_SIZE = 12;
    var SAMPLE_SIZE = 13;           // 10pt, the header's own size
    var CELL_SIZE = 12;             // 9pt, the sample cell's
    var PAD = 6;
    var SAMPLE_WIDTH = 32;
    var HEADER_GREY = "#4b5158";
    var STRIPE = "#f5f5f5";
    var ANCESTRAL = "#ffdada";
    var GRID = "#dddddd";
    var BASELINE = "#c8c8c8";
    var HEAT = ["#ffffd4", "#41b6c4", "#253494"];

    var measurer = document.createElement("canvas").getContext("2d");
    function textWidth(text, size, bold) {
        measurer.font = (bold ? "bold " : "") + size + "px " + FONT;
        return measurer.measureText(text).width;
    }

    function escape(text) {
        return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    /* A cell's HTML as the words it shows: buttons dropped, and anything hidden -- a long
       deletion's collapsed gene list -- dropped with them, so it reads "N genes" as it
       does on screen. */
    function plainText(html) {
        if (html === null || html === undefined) { return ""; }
        var holder = document.createElement("div");
        holder.innerHTML = String(html);
        Array.prototype.forEach.call(
            holder.querySelectorAll("button, [hidden], [style*='display: none'], [style*='display:none']"),
            function (el) { el.remove(); });
        return holder.textContent.replace(/\s+/g, " ").trim();
    }

    /* sRGB hex <-> oklab, for the heat map's mix. */
    function toLinear(c) { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
    function fromLinear(c) { return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; }
    function hexToOklab(hex) {
        var r = toLinear(parseInt(hex.substr(1, 2), 16) / 255);
        var g = toLinear(parseInt(hex.substr(3, 2), 16) / 255);
        var b = toLinear(parseInt(hex.substr(5, 2), 16) / 255);
        var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
        var m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
        var s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
        return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
    }
    function oklabToHex(lab) {
        var l = Math.pow(lab[0] + 0.3963377774 * lab[1] + 0.2158037573 * lab[2], 3);
        var m = Math.pow(lab[0] - 0.1055613458 * lab[1] - 0.0638541728 * lab[2], 3);
        var s = Math.pow(lab[0] - 0.0894841775 * lab[1] - 1.2914855480 * lab[2], 3);
        var rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                   -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                   -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s];
        return "#" + rgb.map(function (c) {
            var v = Math.round(Math.min(1, Math.max(0, fromLinear(c))) * 255);
            return (v < 16 ? "0" : "") + v.toString(16);
        }).join("");
    }
    function mix(a, b, t) {
        var x = hexToOklab(a), y = hexToOklab(b);
        return oklabToHex([0, 1, 2].map(function (i) { return x[i] + (y[i] - x[i]) * t; }));
    }
    /* The stylesheet's two mixes: yellow to teal over the first half, teal to blue over
       the second. */
    function heatColor(f) {
        var t1 = Math.min(1, Math.max(0, f * 2)), t2 = Math.min(1, Math.max(0, f * 2 - 1));
        return mix(mix(HEAT[0], HEAT[1], t1), HEAT[2], t2);
    }

    /* A computed color as hex; `rgb(51, 122, 183)` is what getComputedStyle answers. */
    function cssHex(value, fallback) {
        var match = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(value || "");
        if (!match) {
            return /^#[0-9a-f]{6}$/i.test((value || "").trim()) ? value.trim() : fallback;
        }
        return "#" + match.slice(1, 4).map(function (n) {
            var v = parseInt(n, 10);
            return (v < 16 ? "0" : "") + v.toString(16);
        }).join("");
    }
    function sampleColor(th) {
        var value = getComputedStyle(th).getPropertyValue("--sample-color").trim();
        if (/^#[0-9a-f]{6}$/i.test(value)) { return value; }
        return cssHex(getComputedStyle(th).backgroundColor, "#337ab7");
    }

    /* The sample's name without its flag badges. */
    function sampleLabel(th) {
        var link = th.querySelector("a") || th;
        var copy = link.cloneNode(true);
        Array.prototype.forEach.call(copy.querySelectorAll(".sample-flag"), function (el) { el.remove(); });
        return copy.textContent.replace(/\s+/g, " ").trim();
    }

    /* What a number cell shows: `42` for 42.0%, the check mark as it is. */
    function compact(cell) {
        if (typeof cell.s !== "number" || cell.f.indexOf("%") < 0) { return cell.f; }
        return String(Math.round(cell.s * 100));
    }

    function currentClass(table, prefix, fallback) {
        for (var i = 0; i < table.classList.length; i++) {
            if (table.classList[i].indexOf(prefix) === 0) {
                return table.classList[i].slice(prefix.length);
            }
        }
        return fallback;
    }

    function mutationMatrixSvg(dt, table) {
        var format = currentClass(table, "freq-", "number");
        var normal = currentClass(table, "view-", "condensed") === "normal";
        var rowHeight = normal ? 38 : 22;
        var barHeight = normal ? 34 : 20;

        var rows = dt.rows({ search: "applied" }).data().toArray();

        // The visible columns, in table order, each with what it needs to draw. Headers come
        // from DataTables rather than the DOM: a hidden column's <th> is taken out of the
        // document, so a fresh querySelectorAll would be short and every index after it off.
        var columns = [];
        dt.columns().indexes().toArray().forEach(function (i) {
            if (!dt.column(i).visible()) { return; }
            var th = dt.column(i).header();
            if (th.getAttribute("data-key") !== null) {
                var key = th.getAttribute("data-key");
                var texts = rows.map(function (row) { return plainText(row[key]); });
                var title = th.textContent.trim();
                var width = texts.reduce(function (w, t) {
                    return Math.max(w, textWidth(t, BODY_SIZE, false));
                }, textWidth(title, BODY_SIZE, true));
                columns.push({ kind: "text", title: title, texts: texts,
                               width: Math.ceil(width) + 2 * PAD });
            } else {
                columns.push({ kind: "sample", title: sampleLabel(th), color: sampleColor(th),
                               index: parseInt(th.getAttribute("data-index"), 10),
                               width: SAMPLE_WIDTH });
            }
        });

        var samples = columns.filter(function (c) { return c.kind === "sample"; });
        var headerHeight = Math.max(rowHeight, Math.ceil(samples.reduce(function (h, c) {
            return Math.max(h, textWidth(c.title, SAMPLE_SIZE, true));
        }, 0)) + 2 * PAD);
        var x = 0;
        columns.forEach(function (c) { c.x = x; x += c.width; });
        var width = Math.max(x, 1);
        var height = headerHeight + rows.length * rowHeight;

        var out = [];
        out.push('<?xml version="1.0" encoding="UTF-8"?>');
        out.push('<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height +
                 '" viewBox="0 0 ' + width + " " + height + '" font-family="' + FONT + '">');
        out.push('<rect x="0" y="0" width="' + width + '" height="' + height + '" fill="#ffffff"/>');

        // The header: grey behind the descriptive titles, each sample on its own color with
        // its name written bottom to top.
        columns.forEach(function (c) {
            out.push('<rect x="' + c.x + '" y="0" width="' + c.width + '" height="' + headerHeight +
                     '" fill="' + (c.kind === "sample" ? c.color : HEADER_GREY) + '"/>');
            if (c.kind === "text") {
                out.push('<text x="' + (c.x + PAD) + '" y="' + (headerHeight - PAD) +
                         '" font-size="' + BODY_SIZE + '" font-weight="bold" fill="#ffffff">' +
                         escape(c.title) + "</text>");
            } else {
                var cx = c.x + c.width / 2 + SAMPLE_SIZE * 0.35, cy = headerHeight - PAD;
                out.push('<text x="' + cx + '" y="' + cy + '" transform="rotate(-90 ' + cx + " " + cy +
                         ')" font-size="' + SAMPLE_SIZE + '" font-weight="bold" fill="#ffffff">' +
                         escape(c.title) + "</text>");
            }
        });

        rows.forEach(function (row, r) {
            var top = headerHeight + r * rowHeight;
            var ground = row.ancestral ? ANCESTRAL : (r % 2 ? STRIPE : null);
            if (ground) {
                out.push('<rect x="0" y="' + top + '" width="' + width + '" height="' + rowHeight +
                         '" fill="' + ground + '"/>');
            }
            var middle = top + rowHeight / 2;
            columns.forEach(function (c) {
                if (c.kind === "text") {
                    if (c.texts[r]) {
                        out.push('<text x="' + (c.x + PAD) + '" y="' + middle +
                                 '" dominant-baseline="central" font-size="' + BODY_SIZE +
                                 '" fill="#333333">' + escape(c.texts[r]) + "</text>");
                    }
                    return;
                }
                var cell = row.samples && row.samples[c.index];
                if (!cell) { return; }
                var f = typeof cell.s === "number" ? cell.s : 1;
                var cellTop = middle - barHeight / 2;
                if (format === "bars") {
                    var fill = cell.p ? mix("#ffffff", c.color, 0.5) : c.color;
                    var h = f * barHeight;
                    out.push('<rect x="' + (c.x + 5) + '" y="' + (cellTop + barHeight - h) +
                             '" width="' + (c.width - 10) + '" height="' + h + '" fill="' + fill + '"/>');
                    out.push('<line x1="' + (c.x + 5) + '" x2="' + (c.x + c.width - 5) +
                             '" y1="' + (cellTop + barHeight) + '" y2="' + (cellTop + barHeight) +
                             '" stroke="' + BASELINE + '" stroke-width="1"/>');
                    return;
                }
                if (format === "heat" || format === "both") {
                    out.push('<rect x="' + c.x + '" y="' + top + '" width="' + c.width +
                             '" height="' + rowHeight + '" fill="' + heatColor(f) + '"/>');
                }
                if (format === "number" || format === "both") {
                    var ink = format === "both" && f > 0.55 ? "#ffffff" : "#333333";
                    out.push('<text x="' + (c.x + c.width / 2) + '" y="' + middle +
                             '" text-anchor="middle" dominant-baseline="central" font-size="' +
                             CELL_SIZE + '" fill="' + ink + '">' + escape(compact(cell)) + "</text>");
                }
            });
            out.push('<line x1="0" x2="' + width + '" y1="' + (top + rowHeight) + '" y2="' +
                     (top + rowHeight) + '" stroke="' + GRID + '" stroke-width="0.5"/>');
        });

        out.push("</svg>");
        return out.join("\n");
    }

    window.mutationMatrixSvg = mutationMatrixSvg;
})();

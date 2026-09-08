/* The client half of mutint_common.preferences: how a page remembers what a reader chose.
 *
 *   var prefs = window.mutintPreferences({authenticated: true|false, url: "/preferences/",
 *                                         embedded: {key: value, ...}});
 *   prefs.get(key, fallback);   prefs.set(key, value);
 *
 * A signed-in reader's choices live in the database. The page embeds the ones it needs as a
 * json_script (`embedded`), so the first draw is already the one they asked for, and a change
 * is saved through mutintPostJson -- fire and forget, because the page already shows the
 * choice and a save that failed costs a reload's worth of memory, not correctness. An
 * anonymous reader gets localStorage under `mutint.<key>`, one browser's memory.
 *
 * Two helpers ride along because every caller needs them: `embedded(id)` reads a json_script
 * node by id, and `hiddenSet({hidden: [...]})` turns a stored choice into a lookup. Choices
 * are stored as the *hidden* set, never the visible one, so a column, sample, type or
 * reference that did not exist when the choice was made shows by default.
 *
 * Used by mutation_matrix.js (Compare and Search) and breseq_references.js (the per-sample
 * Mutations page), which share `mutation_matrix.references.<experiment>` between them. Loaded
 * from base.html after mutint_crud.js, which owns mutintPostJson and the CSRF cookie read.
 */
(function () {
    "use strict";

    function store(options) {
        var authed = !!options.authenticated;
        var url = options.url;
        var embedded = options.embedded || {};
        return {
            get: function (key, fallback) {
                if (authed) {
                    return Object.prototype.hasOwnProperty.call(embedded, key) ? embedded[key] : fallback;
                }
                try {
                    var raw = window.localStorage.getItem("mutint." + key);
                    return raw ? JSON.parse(raw) : fallback;
                } catch (e) { return fallback; }
            },
            set: function (key, value) {
                if (authed) {
                    window.mutintPostJson(url, { key: key, value: value }).catch(function () {});
                    return;
                }
                try { window.localStorage.setItem("mutint." + key, JSON.stringify(value)); } catch (e) { /* private mode, quota */ }
            }
        };
    }

    store.embedded = function (id) {
        var node = document.getElementById(id);
        if (!node) { return {}; }
        try { return JSON.parse(node.textContent) || {}; } catch (e) { return {}; }
    };

    store.hiddenSet = function (stored) {
        var hidden = stored && Array.isArray(stored.hidden) ? stored.hidden : [];
        var set = {};
        hidden.forEach(function (value) { set[String(value)] = true; });
        return set;
    };

    window.mutintPreferences = store;
}());

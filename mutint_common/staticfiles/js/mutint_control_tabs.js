/* The tab strip above a mutation table (control_tabs.html): restore the tab the reader left
 * open, and remember the one they choose.
 *
 * `<page>.tab` in the preference store (mutint_preferences.js): embedded for a signed-in
 * reader -- the matrix embeds its whole `mutation_matrix.` prefix, the per-sample view embeds
 * the key -- and the browser's own storage otherwise. Restoring toggles the classes itself rather than
 * calling the plugin, so it runs at DOMContentLoaded, before the DataTables bundle that
 * carries Bootstrap's tab plugin has loaded at the end of body. A remembered tab this page
 * does not offer leaves the page's own default standing.
 *
 * Saving listens for the plugin's `shown.bs.tab`, which is triggered through jQuery and
 * invisible to addEventListener; delegated from the document, because the plugin binds after
 * this ran.
 */
(function () {
    "use strict";

    function storeFor(strip) {
        return window.mutintPreferences({
            authenticated: strip.getAttribute("data-authenticated") === "1",
            url: strip.getAttribute("data-preferences-url"),
            embedded: window.mutintPreferences.embedded(strip.getAttribute("data-prefs-id"))
        });
    }

    function keyFor(strip) { return strip.getAttribute("data-control-tabs") + ".tab"; }

    function restore(strip) {
        var stored = storeFor(strip).get(keyFor(strip), null);
        var links = Array.prototype.slice.call(strip.querySelectorAll('a[data-toggle="tab"]'));
        var chosen = stored && stored.tab && links.filter(function (a) {
            return a.getAttribute("data-tab") === stored.tab;
        })[0];
        if (!chosen) { return; }
        links.forEach(function (a) {
            var on = a === chosen;
            var pane = document.querySelector(a.getAttribute("href"));
            a.parentNode.classList.toggle("active", on);
            if (pane) { pane.classList.toggle("active", on); }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        Array.prototype.forEach.call(document.querySelectorAll("[data-control-tabs]"), restore);
    });

    $(document).on("shown.bs.tab", '[data-control-tabs] a[data-toggle="tab"]', function () {
        var strip = $(this).closest("[data-control-tabs]")[0];
        storeFor(strip).set(keyFor(strip), { tab: this.getAttribute("data-tab") });
    });
}());

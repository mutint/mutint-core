/* The Show/Hide button on a large deletion's gene list, as breseq's report has.
 *
 * This lives in a file rather than inline in a template because the markup it drives is
 * generated in Python -- aledb_import.annotate.display._collapsed_gene_list -- and so turns
 * up wherever a breseq-style row is rendered: the Samples page, the genome browser and the
 * mutation editor's Edit/Delete listing all render it from the same build_rows(). It used to
 * be an inline script in the Samples page's template, which is exactly one of those three, so
 * on the other two the button rendered, was styled, and did nothing when clicked. It travels
 * with breseq_table.css now: link the stylesheet, load this.
 *
 * Delegated from the document rather than bound per row, for two reasons. The markup emits no
 * element ids -- breseq numbers each block `gene_hide_<type>_<id>` and wires an inline onclick
 * to a global function, which needs ids that stay unique inside a page it does not own -- and
 * the editor's Delete listing is a DataTable with `deferRender`, so a row on an undrawn page
 * has no DOM to bind to yet.
 *
 * `hidden` is the plain HTML attribute, not Bootstrap's .hidden, which carries !important and
 * so could not be toggled back off.
 */
(function () {
    "use strict";

    document.addEventListener('click', function (event) {
        var button = event.target.closest('.breseq_gene_toggle');
        if (!button) { return; }
        var list = button.parentNode.querySelector('.breseq_gene_list');
        if (!list) { return; }
        var showing = !list.hasAttribute('hidden');
        if (showing) { list.setAttribute('hidden', ''); } else { list.removeAttribute('hidden'); }
        button.textContent = showing ? 'Show' : 'Hide';
    });
}());

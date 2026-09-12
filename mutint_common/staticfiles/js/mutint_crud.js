/* Shared helpers for the create/delete controls on the project and experiment pages.
 *
 * The create forms are Bootstrap modals, opened declaratively with data-toggle, so
 * there is no panel-toggling helper here any more -- an inline panel expanded into
 * the DataTable below it, which does not reflow, and the two overlapped.
 *
 * Was duplicated inline, byte for byte, in project/list.html and experiment/list.html;
 * project/detail.html would have made a third copy. Loaded from base.html, so every
 * page has it. mutintDeleteSelected joined them for exactly that reason: the whole
 * gather-confirm-post-reload routine was inline in those same two templates, and the
 * project page wanting it as well is what turned two copies into an argument for none.
 *
 * Two confirm dialogs, and which one a control gets is decided by one question: can the
 * person undo it afterwards? mutintConfirm(title, text, verb) is a plain accept, for every
 * removal that can be put back -- a soft-deleted project or experiment (an administrator
 * restores it until it is purged), a revoked grant, a group membership, a mutation delete
 * the history page restores. mutintConfirmTyped(title, text, verb, word) makes the person
 * type a word, and is for what cannot: leaving a project types LEAVE, because nobody but an
 * administrator can let you back in; clearing stored files types CLEAR, because the files
 * are gone and only re-importing brings them. The word names the action rather than being
 * DELETE everywhere, so the dialog cannot be answered by reflex.
 *
 * mutintPost sends the CSRF token from the cookie. A page using it must render
 * {% csrf_token %} somewhere, which is what sets that cookie. mutintPostJson is the same
 * request with a JSON body instead of FormData, for the endpoints that take a structure --
 * mutintPost stringifies every value, so a list or a nested object cannot survive it.
 *
 * Both live here because reading that cookie should have one definition. mutint_upload.js and
 * import/add.html each grew their own for want of one, which is three regexes that have to
 * agree about the same string.
 */
(function () {
    "use strict";

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[2]) : "";
    }

    window.mutintPostJson = function (url, payload) {
        return fetch(url, {
            method: "POST",
            headers: { "X-CSRFToken": getCookie("csrftoken"),
                       "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function (resp) {
            return resp.json().then(function (body) {
                if (!resp.ok) {
                    var failure = new Error(body.error || ("HTTP " + resp.status));
                    failure.body = body;
                    throw failure;
                }
                return body;
            });
        });
    };

    /* The CSRF header every hand-rolled request here needs, so an XHR can set it too. */
    window.mutintCsrfHeader = function () {
        return getCookie("csrftoken");
    };

    window.mutintPost = function (url, data) {
        var form = new FormData();
        Object.keys(data || {}).forEach(function (k) { form.append(k, data[k]); });
        return fetch(url, {
            method: "POST",
            headers: { "X-CSRFToken": getCookie("csrftoken") },
            body: form
        }).then(function (resp) {
            return resp.json().then(function (body) {
                if (!resp.ok) {
                    // The parsed body rides along on the Error. Callers that only read
                    // err.message are unaffected; the sample table needs err.body.errors
                    // to outline which rows it refused, and the body used to be dropped
                    // here.
                    var failure = new Error(body.error || ("HTTP " + resp.status));
                    failure.body = body;
                    throw failure;
                }
                return body;
            });
        });
    };

    /* The plain accept, for a removal that can be undone.
     *
     * Deliberately client-side only, as the typed one is. The endpoints already check the
     * role and the experiment lock, which is what actually protects the data; a dialog
     * guards against the mis-aimed click and nothing more.
     */
    window.mutintConfirm = function (title, text, verb) {
        return swal({
            title: title,
            text: text,
            icon: "warning",
            buttons: { cancel: true, confirm: { text: verb, closeModal: true } },
            dangerMode: true
        }).then(function (confirmed) { return Boolean(confirmed); });
    };

    /* The stock wording for deleting a project or an experiment -- the two bulk deletes,
     * the project list's and Delete experiment on /stats. Plain, because deletion is soft:
     * the row leaves every listing and an administrator can bring it back until it is
     * purged. Worded without a number, because `what` supplies one: this dialog says
     * "2 experiment(s)" and "this experiment" from the same sentence. */
    window.mutintConfirmDelete = function (what) {
        return window.mutintConfirm("Delete " + what + "?",
            "It leaves every listing. An administrator can bring it back until it is "
            + "purged.", "Delete");
    };

    /* The typed dialog, for what cannot be undone.
     *
     * title  the question, e.g. "Leave this project?"
     * text   what happens, ending in "Type <word> to confirm." -- said by the caller so the
     *        sentence is true of what this particular control does.
     * verb   the confirm button's label.
     * word   what must be typed, in capitals, naming the action: LEAVE, CLEAR.
     * Resolves true only for the exact word; false when dismissed or mistyped, saying so
     * in the second case only (see below). */
    window.mutintConfirmTyped = function (title, text, verb, word) {
        return swal({
            title: title,
            text: text,
            icon: "warning",
            content: { element: "input", attributes: { placeholder: word } },
            buttons: { cancel: true, confirm: { text: verb, closeModal: true } },
            dangerMode: true
        }).then(function (typed) {
            // sweetalert resolves the input's *value* here rather than a boolean: null when
            // the dialog is dismissed, "" when confirmed with an empty box. Both are falsy,
            // so the `if (!confirmed)` idiom the callers use would swallow the second
            // silently -- and somebody who pressed the button and saw nothing happen has no
            // way to tell that from a page that is broken. The two are separated so only
            // one of them says anything.
            if (typed === null) { return false; }
            if (typed.trim() !== word) {
                swal("", "Nothing was changed -- type " + word + " exactly to confirm.",
                     "info");
                return false;
            }
            return true;
        });
    };

    /* The stock typed wording for the Storage panel's Clear buttons. Files go and cannot be
     * recovered short of re-importing, which is what makes this the typed dialog; the
     * samples and their mutations stay, which the sentence says. */
    window.mutintConfirmTypedClear = function (what) {
        return window.mutintConfirmTyped("Clear " + what + "?",
            "The files are removed from the store and cannot be recovered; the samples "
            + "and their mutations stay. Type CLEAR to confirm.", "Clear", "CLEAR");
    };

    /* "Delete selected" over a DataTable of rows that each link to what they are.
     *
     * opts: {tableId, path, noun} -- e.g. {tableId: "exp_table",
     *        path: "/experiment/", noun: "experiment"}.
     *
     * Three pages want this and it was inline in two of them, near enough byte for byte;
     * project/detail.html would have made a third copy, which is the reason this file
     * exists at all.
     *
     * The id comes from the row's own anchor rather than from a cell, so the two tables'
     * differing column orders do not matter here.
     */
    window.mutintDeleteSelected = function (opts) {
        var table = $("#" + opts.tableId).DataTable();
        var ids = [];
        table.rows({selected: true}).every(function () {
            var link = $(this.node()).find("a[href*='" + opts.path + "']").attr("href");
            if (link) {
                var match = link.match(new RegExp(opts.path.replace(/\//g, "\\/") + "(\\d+)"));
                if (match) { ids.push(match[1]); }
            }
        });
        if (!ids.length) {
            swal("", "Please select " + opts.noun + "s and try again.", "warning");
            return;
        }
        window.mutintConfirmDelete(ids.length + " " + opts.noun + "(s)")
            .then(function (confirmed) {
                if (!confirmed) { return; }
                // allSettled, not all: these lists show everything the user may *view*,
                // while deleting takes a role they may not hold -- and an experiment may be
                // locked -- so a mixed selection legitimately 403s on some rows.
                // Promise.all would abort on the first of those after others had already
                // been deleted, and then never reload.
                Promise.allSettled(ids.map(function (id) {
                    return window.mutintPost(opts.path + id + "/delete/", {});
                })).then(function (results) {
                    var failed = results.filter(function (r) {
                        return r.status === "rejected";
                    });
                    if (failed.length) {
                        document.getElementById("del-error").textContent =
                            failed.length + " of " + ids.length + " could not be deleted: " +
                            failed[0].reason.message;
                    }
                    window.location.reload();
                });
            });
    };
})();

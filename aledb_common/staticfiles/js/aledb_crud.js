/* Shared helpers for the create/delete controls on the project and experiment pages.
 *
 * The create forms are Bootstrap modals, opened declaratively with data-toggle, so
 * there is no panel-toggling helper here any more -- an inline panel expanded into
 * the DataTable below it, which does not reflow, and the two overlapped.
 *
 * Was duplicated inline, byte for byte, in ale/projects.html and ale/experiments.html;
 * ale/project_detail.html would have made a third copy. Loaded from base.html, so every
 * page has it. aledbDeleteSelected joined them for exactly that reason: the whole
 * gather-confirm-post-reload routine was inline in those same two templates, and the
 * project page wanting it as well is what turned two copies into an argument for none.
 *
 * There are two confirm dialogs and the difference between them is deliberate:
 * aledbConfirmDelete is a plain yes/no, for revoking a grant or a group membership;
 * aledbConfirmTypedDelete makes the person type DELETE, for the four controls that
 * destroy data.
 *
 * aledbPost sends the CSRF token from the cookie. A page using it must render
 * {% csrf_token %} somewhere, which is what sets that cookie. aledbPostJson is the same
 * request with a JSON body instead of FormData, for the endpoints that take a structure --
 * aledbPost stringifies every value, so a list or a nested object cannot survive it.
 *
 * Both live here because reading that cookie should have one definition. aledb_upload.js and
 * import/add.html each grew their own for want of one, which is three regexes that have to
 * agree about the same string.
 */
(function () {
    "use strict";

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[2]) : "";
    }

    window.aledbPostJson = function (url, payload) {
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
    window.aledbCsrfHeader = function () {
        return getCookie("csrftoken");
    };

    window.aledbPost = function (url, data) {
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

    window.aledbConfirmDelete = function (what) {
        return swal({
            title: "Delete " + what + "?",
            text: "Are you sure you want to delete. This is permanent.",
            icon: "warning",
            buttons: true,
            dangerMode: true
        });
    };

    /* The same dialog, gated on the person typing DELETE.
     *
     * For the four controls that delete *data* -- a project, or an experiment and with it
     * every sample, mutation and alignment ever imported into it. aledbConfirmDelete above
     * stays for revoking a grant or removing someone from a group, which destroy nothing;
     * a dialog that feels the same for both teaches people to click through this one.
     *
     * Deliberately client-side only. The endpoints already check the role and the
     * experiment lock, which is what actually protects the data; this guards against the
     * mis-aimed click, and a server-side "must post DELETE" field would be a contract
     * `./aledb delete` does not honour and one curl away from bypass anyway.
     */
    window.aledbConfirmTypedDelete = function (what) {
        return swal({
            title: "Delete " + what + "?",
            // Worded without a number, because `what` supplies one: this dialog says
            // "2 experiment(s)" and "this experiment" from the same sentence.
            text: "Deleted data leaves every listing, and only an administrator can bring "
                + "it back. Type DELETE to confirm.",
            icon: "warning",
            content: { element: "input", attributes: { placeholder: "DELETE" } },
            buttons: { cancel: true, confirm: { text: "Delete", closeModal: true } },
            dangerMode: true
        }).then(function (typed) {
            // sweetalert resolves the input's *value* here rather than a boolean: null when
            // the dialog is dismissed, "" when confirmed with an empty box. Both are falsy,
            // so the `if (!confirmed)` idiom the plain helper's callers use would swallow
            // the second silently -- and somebody who pressed Delete and saw nothing happen
            // has no way to tell that from a page that is broken. The two are separated so
            // only one of them says anything.
            if (typed === null) { return false; }
            if (typed.trim() !== "DELETE") {
                swal("", "Nothing was deleted -- type DELETE exactly to confirm.", "info");
                return false;
            }
            return true;
        });
    };

    /* "Delete selected" over a DataTable of rows that each link to what they are.
     *
     * opts: {tableId, path, noun} -- e.g. {tableId: "exp_table",
     *        path: "/experiment/", noun: "experiment"}.
     *
     * Three pages want this and it was inline in two of them, near enough byte for byte;
     * ale/project_detail.html would have made a third copy, which is the reason this file
     * exists at all.
     *
     * The id comes from the row's own anchor rather than from a cell, so the two tables'
     * differing column orders do not matter here.
     */
    window.aledbDeleteSelected = function (opts) {
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
        window.aledbConfirmTypedDelete(ids.length + " " + opts.noun + "(s)")
            .then(function (confirmed) {
                if (!confirmed) { return; }
                // allSettled, not all: these lists show everything the user may *view*,
                // while deleting takes a role they may not hold -- and an experiment may be
                // locked -- so a mixed selection legitimately 403s on some rows.
                // Promise.all would abort on the first of those after others had already
                // been deleted, and then never reload.
                Promise.allSettled(ids.map(function (id) {
                    return window.aledbPost(opts.path + id + "/delete/", {});
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

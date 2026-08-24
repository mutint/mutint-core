/* Shared helpers for the create/delete controls on the project and experiment pages.
 *
 * The create forms are Bootstrap modals, opened declaratively with data-toggle, so
 * there is no panel-toggling helper here any more -- an inline panel expanded into
 * the DataTable below it, which does not reflow, and the two overlapped.
 *
 * Was duplicated inline, byte for byte, in ale/projects.html and ale/experiments.html;
 * ale/project_detail.html would have made a third copy. Loaded from base.html, so every
 * page has it.
 *
 * aledbPost sends the CSRF token from the cookie. A page using it must render
 * {% csrf_token %} somewhere, which is what sets that cookie.
 */
(function () {
    "use strict";

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[2]) : "";
    }

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
})();

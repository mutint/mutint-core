/* Shared helpers for the create/delete controls on the project and experiment pages.
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
                if (!resp.ok) { throw new Error(body.error || ("HTTP " + resp.status)); }
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

    /* Wire a "+ New ..." button to the panel it toggles. */
    window.aledbTogglePanel = function (buttonId, panelId, cancelId) {
        var panel = document.getElementById(panelId);
        var button = document.getElementById(buttonId);
        if (!panel || !button) { return; }
        button.addEventListener("click", function () {
            panel.style.display = panel.style.display === "none" ? "block" : "none";
        });
        if (cancelId && document.getElementById(cancelId)) {
            document.getElementById(cancelId).addEventListener("click", function () {
                panel.style.display = "none";
            });
        }
    };
})();

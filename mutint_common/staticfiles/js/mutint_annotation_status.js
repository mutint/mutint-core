/* The annotation panel under the Import data tab strip, and the event every tab page hears.
 *
 * A reference annotator that takes minutes -- ISEScan is the one -- enqueues a job and hands
 * back a sentence. This is what turns that sentence into something a person can watch and
 * stop without leaving the page they are on, and it lives beside mutint_upload.js rather than
 * inside import.html because it loads on plugin tab pages too: mutint-breseq's launcher and
 * mutint-refsniff's tab both render {% import_tabs %} and therefore both get this.
 *
 * **It touches no button on any page, and that is deliberate rather than unfinished.** It runs
 * on pages this repo does not own, so reaching for `#import-submit` would be a core snippet
 * depending on an id a plugin is free to reuse for something else. It draws its own panel and
 * dispatches `mutint:annotation-status` on `document`; a page that has a control to hold
 * listens and decides for itself. import.html is the first listener.
 *
 * The initial state is server-rendered into #mutint-annotation-status, so the panel is right
 * the instant the page parses -- which is what covers the reload a first-reference import
 * does, landing the person on some other tab entirely. Only *changes* arrive by event; there
 * is no mutable global for a late script to read stale.
 */
(function () {
    "use strict";

    var panel = document.getElementById("mutint-annotation-panel");
    var dataEl = document.getElementById("mutint-annotation-status");
    if (!panel || !dataEl) { return; }

    var STATUS_URL = panel.getAttribute("data-status-url") || "";
    /* Read off the panel rather than from a job on it: the panel has to be able to ask "has
     * anything started?" when it is holding no jobs at all, which is exactly the state the
     * Update Annotation tab is in the moment somebody presses Run annotators. */
    var EXPERIMENT_ID = panel.getAttribute("data-experiment") || "";
    /* Five seconds, the same heartbeat /jobs/ uses. This watches something that takes
     * minutes, so anything faster is only more requests. */
    var POLL_MS = 5000;
    /* What each queue status is called in front of a person. Mirrors
     * mutint_jobs.queue.STATUS_LABELS, as jobs/list.html's own copy does -- the server sends
     * the queue's word and the page says it in English. */
    var LABELS = {
        READY: "Queued",
        RUNNING: "Running",
        SUCCESSFUL: "Finished",
        FAILED: "Failed",
        unknown: "No longer on the queue"
    };

    var state = null;
    var timer = null;
    /* Bumped whenever polling stops. A request already in the air when the last job finishes
     * resolves *after* the panel was cleared and would redraw it from a snapshot taken before
     * the end -- a warning that reappears by itself, which reads as the job restarting.
     * clearTimeout stops the next tick, not the one in flight, so the answer is to ignore the
     * answer. import.html's upload poll carries the same guard for the same reason. */
    var generation = 0;

    function read() {
        try {
            return JSON.parse(dataEl.textContent) || null;
        } catch (err) {
            return null;
        }
    }

    function label(status) {
        return LABELS[status] || status || "";
    }

    function row(job) {
        var line = document.createElement("div");
        line.style.marginTop = "0.3em";

        var name = document.createElement("strong");
        name.textContent = job.label;
        line.appendChild(name);
        line.appendChild(document.createTextNode(" — " + label(job.status)));
        if (job.user) {
            line.appendChild(document.createTextNode(", started by " + job.user));
        }

        if (job.log) {
            line.appendChild(document.createTextNode(" "));
            var log = document.createElement("a");
            log.className = "btn btn-default btn-xs";
            log.href = job.log;
            log.textContent = "Log";
            line.appendChild(log);
        }
        if (job.cancellable) {
            line.appendChild(document.createTextNode(" "));
            var stop = document.createElement("button");
            stop.type = "button";
            stop.className = "btn btn-default btn-xs mutint-annotation-cancel";
            stop.setAttribute("data-job", job.id);
            stop.textContent = "Cancel";
            line.appendChild(stop);
        }
        return line;
    }

    function render() {
        panel.innerHTML = "";
        if (!state || !state.busy) {
            panel.style.display = "none";
            return;
        }

        var heading = document.createElement("div");
        heading.textContent = "Annotating the reference genome.";
        panel.appendChild(heading);

        (state.jobs || []).forEach(function (job) { panel.appendChild(row(job)); });

        var why = document.createElement("div");
        why.style.marginTop = "0.5em";
        why.textContent = "Importing is held until this finishes: what lands now would have " +
            "been called against the reference as it is, and re-annotating afterwards cannot " +
            "undo that.";
        panel.appendChild(why);

        if (state.stalled) {
            /* The same hedge /jobs/ shows, and it matters more here, because this panel is
             * switching a button off: work has been waiting, which means either no worker is
             * running or one is busy on something long. Nothing can tell those apart, so say
             * what was observed and point at the way out. */
            var stalled = document.createElement("div");
            stalled.style.marginTop = "0.5em";
            stalled.textContent = "Nothing has taken work off the queue for a while. If no " +
                "worker is running, Cancel above is the way out.";
            panel.appendChild(stalled);
        }

        panel.style.display = "";
    }

    function announce() {
        document.dispatchEvent(new CustomEvent("mutint:annotation-status", {
            detail: {
                busy: !!(state && state.busy),
                stalled: state ? state.stalled : null,
                jobs: (state && state.jobs) || []
            }
        }));
    }

    function apply(next) {
        state = next;
        render();
        announce();
        schedule();
    }

    function schedule() {
        if (timer) { clearTimeout(timer); timer = null; }
        if (!state || !state.busy || !STATUS_URL) { generation += 1; return; }
        timer = setTimeout(poll, POLL_MS);
    }

    function ask() {
        return fetch(STATUS_URL + "?experiment_id=" + encodeURIComponent(EXPERIMENT_ID),
                     {headers: {"Accept": "application/json"}})
            .then(function (resp) { return resp.ok ? resp.json() : null; });
    }

    function poll() {
        var mine = generation;
        ask().then(function (body) {
            if (mine !== generation || !body) { return; }
            apply(body);
        }).catch(function () {
            /* A poll that failed says nothing about the job. Leave the panel as it is and ask
             * again rather than clearing a warning on the strength of a dropped request. */
            if (mine === generation) { timer = setTimeout(poll, POLL_MS); }
        });
    }

    panel.addEventListener("click", function (event) {
        var button = event.target.closest(".mutint-annotation-cancel");
        if (!button) { return; }
        button.disabled = true;
        window.mutintPostJson("/jobs/" + button.getAttribute("data-job") + "/cancel", {})
            .then(function () { refresh(); })
            .catch(function (err) {
                button.disabled = false;
                window.alert(err.message || String(err));
            });
    });

    /* One immediate poll, whatever the panel currently believes: a cancellation has just
     * changed the answer, and a page that has only this moment enqueued an annotator has a
     * `busy: false` snapshot from before it did. */
    function refresh() {
        if (!STATUS_URL || !EXPERIMENT_ID) { return; }
        generation += 1;
        ask().then(function (body) { if (body) { apply(body); } }).catch(function () {});
    }

    /* A page that has just launched annotators says so, rather than waiting for the next
     * load. This is what covers the Update Annotation tab and the confirm-rename re-post,
     * neither of which reloads. */
    document.addEventListener("mutint:annotators-started", refresh);

    state = read();
    render();
    announce();
    schedule();
})();

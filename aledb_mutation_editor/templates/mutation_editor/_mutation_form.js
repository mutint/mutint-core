{% comment %}
The machinery both mutation forms need, included inside a <script> the way
`aledb_common/templates/table_template.js` is. Django template tags work in here -- and so
does anything tag-shaped inside a // comment, which the engine does not recognize as a
comment at all. Do not write a tag name in one.

What is shared is everything except the submit: which inputs a type shows, the values that
survive a type change, and how a field error is put beside its input. Add and Change differ
only in what they post.
{% endcomment %}
function aledbMutationForm(initial) {
    "use strict";

    var SCHEMA = JSON.parse(document.getElementById("mutation-schema").textContent);
    var TYPES = {};
    SCHEMA.types.forEach(function (entry) { TYPES[entry.name] = entry; });

    var typeSelect = document.getElementById("me-type");
    var inputs = {};
    Array.prototype.forEach.call(
        document.querySelectorAll(".me-input"), function (el) {
            inputs[el.getAttribute("data-name")] = el;
        });

    // Everything ever typed, keyed by the spec's own field name. A field the current type
    // does not use keeps its value here rather than being cleared, so switching away and back
    // restores it -- and because the spec uses one name for one meaning, `new_seq` carrying
    // from SNP to SUB to INS needs no translation table.
    var values = {};

    Object.keys(inputs).forEach(function (name) {
        var el = inputs[name];
        values[name] = (initial && initial[name] !== undefined && initial[name] !== null)
            ? String(initial[name]) : el.value;
        el.addEventListener("input", function () { values[name] = el.value; });
        el.addEventListener("change", function () { values[name] = el.value; });
    });

    function fieldRow(name) {
        return document.querySelector('.me-field[data-field="' + name + '"]');
    }

    function showFieldsFor(typeName) {
        var entry = TYPES[typeName];
        var wanted = {};
        entry.fields.forEach(function (name) { wanted[name] = true; });

        Object.keys(inputs).forEach(function (name) {
            var row = fieldRow(name);
            if (!row) { return; }
            row.style.display = wanted[name] ? "" : "none";
            if (wanted[name]) {
                // Repopulate from the parked value rather than from whatever the input
                // happens to still hold, so the two never disagree.
                inputs[name].value = values[name] || "";
                row.querySelector(".help-block").textContent = SCHEMA.help[name] || "";
            }
        });
        document.getElementById("me-type-help").textContent = entry.label;
    }

    function translateInto(typeName) {
        // A SNP is a one-base SUB, so widening a call should not fail for a blank size. The
        // only translation beyond same-name, and it never overwrites a typed value.
        if (typeName === "SUB" && !values.size) {
            values.size = "1";
        }
    }

    function clearFieldErrors() {
        Array.prototype.forEach.call(
            document.querySelectorAll(".me-field"), function (row) {
                row.classList.remove("has-error");
            });
        document.getElementById("me-error").textContent = "";
    }

    function showFieldErrors(errors) {
        Object.keys(errors || {}).forEach(function (name) {
            var row = fieldRow(name);
            if (!row) { return; }
            row.classList.add("has-error");
            row.querySelector(".help-block").textContent = errors[name];
        });
    }

    typeSelect.addEventListener("change", function () {
        translateInto(typeSelect.value);
        clearFieldErrors();
        showFieldsFor(typeSelect.value);
    });

    if (initial && initial.__type) {
        typeSelect.value = initial.__type;
    }
    showFieldsFor(typeSelect.value);

    return {
        type: function () { return typeSelect.value; },
        clearFieldErrors: clearFieldErrors,
        showFieldErrors: showFieldErrors,
        // Only the chosen type's fields. The parked values for the others stay in the
        // browser; the server would reject them as unknown for this type anyway.
        fields: function () {
            var out = {};
            TYPES[typeSelect.value].fields.forEach(function (name) {
                out[name] = values[name] || "";
            });
            return out;
        }
    };
}

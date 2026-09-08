"""`{% mutation_matrix matrix %}` -- the cross-sample table, on any page that has a matrix.

A tag rather than an include so the partial can learn two things the view need not pass: who is
reading, and what they have already chosen. The reader's stored preferences are embedded in the
page for the script to read synchronously, so the first draw is already the one they asked for
-- a table that draws every column and then hides half of them flashes.

    {% load mutation_matrix %}
    {% mutation_matrix matrix empty_message="No mutations matched." %}

The page must link `css/breseq_table.css` and load `js/breseq_table.js` and
`js/mutation_matrix.js` (see `mutation_matrix/page.html`); `mutint_common.tests.test_templates`
checks that the three travel together.
"""

from django import template
from django.urls import reverse

from mutint_common.preferences import get_preferences

register = template.Library()

PREFERENCE_PREFIX = "mutation_matrix."


@register.inclusion_tag("mutation_matrix/_table.html", takes_context=True)
def mutation_matrix(context, matrix, empty_message="No mutations to show.", controls=True):
    """`controls=False` for a page that renders the tab strip itself -- `mutation_matrix/page.html`
    does, to put a Filter tab holding its form in front of the matrix's three; the tag then
    renders the table alone and the script finds the menus through the page's strip."""
    request = context.get("request")
    user = getattr(request, "user", None)
    authenticated = bool(user is not None and user.is_authenticated)
    return {
        "matrix": matrix,
        "empty_message": empty_message,
        "controls": controls,
        "authenticated": authenticated,
        "preferences": get_preferences(user, PREFERENCE_PREFIX) if authenticated else {},
        "preferences_url": reverse("preferences"),
        "rows_id": matrix.dom_id + "-rows",
        "prefs_id": matrix.dom_id + "-prefs",
        # The tag renders inside the page's own template context only partially, so the
        # CSRF token the save request needs is rendered here rather than assumed.
        "csrf_token": context.get("csrf_token"),
    }

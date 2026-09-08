"""Per-user preferences: what a page remembers about how one person likes to see things.

What is stored here today is the mutation matrix's menus (`mutint_sample.mutation_matrix`):
which descriptive columns and mutation types a reader has hidden, everywhere, and which samples
and reference sequences they have turned off in a particular experiment -- the last shared with
the per-sample Mutations page, which offers the same References menu. Neither belongs in the view filter, which is per session and shapes
*which rows* a page shows; these shape how the same rows are laid out, and a person wants them
to follow them from one experiment to the next and from one machine to another.

**Keys are dotted names owned by whoever writes them** -- `mutation_matrix.columns`,
`mutation_matrix.samples.42` -- so a plugin that wants to remember something picks a prefix of
its own and needs nothing from core but this module. The value is any JSON up to a modest size;
what it means is the key owner's business, and nothing here validates it beyond that.

**Anonymous readers have no row here**, and that is a fact the endpoint states with a 403 rather
than something a page has to guess: ALEdb is a public deployment, so a page that remembers things
keeps a localStorage fallback for readers who are not signed in. `mutint_preferences.js`, the
client half of this module, does that for every page that uses it.

The endpoint answers plain JSON with real HTTP statuses rather than the `@ajax` envelope the tag
endpoints use, because `mutintPostJson` -- the one client that calls it -- reads the status.
"""

import json
import re

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from mutint_common.models import UserPreference

#: Letters, digits, dot, underscore, colon, hyphen. A key is a name, not a path or a query.
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")

#: Generous for a list of hidden columns or sample ids, and small enough that nobody can use a
#: preference as a file store.
MAX_VALUE_BYTES = 16384


class BadPreference(ValueError):
    """The key or the value is not something this store takes."""


def _check_key(key):
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise BadPreference("A preference key is up to 100 characters of letters, digits, "
                            "dot, underscore, colon and hyphen.")


def _check_value(value):
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError):
        raise BadPreference("A preference value must be JSON.")
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise BadPreference("That preference is too large to store.")


def get_preference(user, key, default=None):
    """The value stored under `key` for `user`, or `default`."""
    if user is None or not getattr(user, "is_authenticated", False):
        return default
    row = UserPreference.objects.filter(user=user, key=key).only("value").first()
    return default if row is None else row.value


def get_preferences(user, prefix=""):
    """Every preference of `user` whose key starts with `prefix`, as `{key: value}`."""
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    rows = UserPreference.objects.filter(user=user, key__startswith=prefix)
    return {row.key: row.value for row in rows}


def set_preference(user, key, value):
    """Store `value` under `key` for `user`; `None` forgets the key.

    Raises `BadPreference` for a key or value the store does not take.
    """
    _check_key(key)
    if value is None:
        UserPreference.objects.filter(user=user, key=key).delete()
        return None
    _check_value(value)
    UserPreference.objects.update_or_create(user=user, key=key, defaults={"value": value})
    return value


@require_http_methods(["GET", "POST"])
def preferences(request):
    """The endpoint a page reads and saves through.

    GET `?prefix=mutation_matrix.` answers `{"preferences": {key: value, ...}}`. POST a JSON
    body `{"key": ..., "value": ...}` stores it (a `null` value forgets the key) and answers the
    pair back. Anonymous callers get 403, which is the signal to fall back to the browser.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Sign in to have your preferences remembered."},
                            status=403)

    if request.method == "GET":
        return JsonResponse({"preferences": get_preferences(
            request.user, request.GET.get("prefix", ""))})

    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "The body must be JSON."}, status=400)
    if not isinstance(body, dict) or "key" not in body:
        return JsonResponse({"error": "Send {\"key\": ..., \"value\": ...}."}, status=400)
    try:
        value = set_preference(request.user, body["key"], body.get("value"))
    except BadPreference as bad:
        return JsonResponse({"error": str(bad)}, status=400)
    return JsonResponse({"key": body["key"], "value": value})

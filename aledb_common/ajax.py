"""The JSON envelope the mutation table's curation endpoints answer in.

`{"status": 403, "statusText": "FORBIDDEN", "content": "..."}`, sent as HTTP **200** whatever
the real status was, so a caller reads the outcome out of the body. That contract is not new
and is not ours to choose: `table_template.js` has always read `result['content']` for the
message it shows, and `aledb_seq/tests/test_table_actions.py` reads `status` out of the body
for the same reason.

**This replaces the `djangoajax` package, which was abandoned.** Version 3.3 was its last
release; it declares no `requires_python` and no Django version at all, so it was the one live
dependency with nothing to say about whether it survives a framework upgrade. What it did for
us is the twenty lines below, so keeping it meant carrying an unmaintained package across a
Django major bump to avoid writing them.

Two behaviours are deliberately preserved rather than tidied, because callers depend on both:

- **The `X-Requested-With` header is mandatory.** Without it the answer is a bare 400 rather
  than an envelope -- `@ajax(mandatory=True)` was the default and every caller is jQuery's
  `$.ajax`, which sets the header itself.
- **A view may return a string or an `HttpResponse`.** The curation endpoints return the
  literal `'ok'` on success and an `HttpResponseForbidden` carrying the refusal text, and both
  have to arrive as `content`.

`statusText` comes from `http.HTTPStatus`, which is the same wording the package carried in a
hand-written table.
"""

import json
import logging
from functools import wraps
from http import HTTPStatus

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse

logger = logging.getLogger(__name__)

#: What a caller is told when a view raises. The exception itself is logged, not sent: it may
#: name tables, columns or paths, and the person clicking a tag can do nothing with it.
ERROR_CONTENT = "An error occurred while processing an AJAX request."


def _status_text(status):
    try:
        return HTTPStatus(status).phrase.upper()
    except ValueError:
        return "UNKNOWN STATUS CODE"


def envelope(result):
    """`result` -- a string, a dict, or an `HttpResponse` -- as the envelope dict."""
    if isinstance(result, HttpResponse):
        return {"status": result.status_code,
                "statusText": _status_text(result.status_code),
                "content": result.content.decode(result.charset or "utf-8")}
    return {"status": 200, "statusText": "OK", "content": result}


def ajax(view):
    """Wrap `view`'s return value in the envelope above, always answering HTTP 200."""

    @wraps(view)
    def inner(request, *args, **kwargs):
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            return HttpResponseBadRequest()
        try:
            result = view(request, *args, **kwargs)
        except Exception:
            # Logged rather than propagated, because the envelope is the contract: a caller
            # that gets a Django error page where it expected JSON shows nothing at all.
            logger.exception("AJAX view %s failed", getattr(view, "__name__", view))
            content = ERROR_CONTENT
            if settings.DEBUG:
                import traceback
                content = traceback.format_exc()
            return JsonResponse({"status": 500,
                                 "statusText": _status_text(500),
                                 "content": content})
        return JsonResponse(envelope(result), encoder=_Encoder)

    return inner


class _Encoder(json.JSONEncoder):
    """Falls back to `str()`, so a view returning a model or a Decimal still serialises."""

    def default(self, obj):
        return str(obj)

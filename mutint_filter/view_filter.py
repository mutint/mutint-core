"""The filter a reader is currently looking through, and where it is kept.

**Filtering is per-reader and ephemeral.** It shapes the page you are looking at and the file you
download from it, it lives in your session, and it is stored in no table of ours. `AleExperimentFilter`
stood here before: one row per experiment, editable by anyone with write access, so changing your
own view changed everyone's -- silently, with no record of who did it. That conflated curating a
dataset, which is `mutint_mutation_editor`'s job and is logged and reversible, with choosing what
you personally want to look at, which is nobody else's business.

This module is the *value* layer and `util.py` is the *query* layer, deliberately apart: a plugin
importing the filter should not drag in `MutationCall`'s joins, and these tests need no
database at all -- where pinning the gene-subset rule used to take six model rows.

## The value

Three fields, normalized at construction. `min_freq`/`max_freq` are percentages, matched against
`MutationCall.frequency`, which is a fraction -- the division by 100 happens in `util.py`.

**Normalization collapses two facts into one.** A stored row could be 0-100 with no genes, which
is *configured* and hides nothing, so `describe_filters` had to distinguish "a filter exists" from
"a filter does something" and every reader of that dict had to remember which it wanted. `0` and
`100` normalize to `None` here, so a filter that hides nothing simply equals `EMPTY`. What is lost
is the ability to say "you configured 0-100", which nobody ever wanted said.

## Where it lives

`request.session`, under one key holding every experiment the reader has filtered. **This is the
repo's first session write** -- `request.session` appears once elsewhere, a read of `session_key`
for a log field -- so the traps are written out here rather than left to be rediscovered:

- **The session uses `JSONSerializer`.** Only JSON-native types survive: no `set`, no dataclass, no
  `Decimal`. `to_session_dict` exists for that reason.
- **Integer dict keys come back as strings.** Keying by `experiment_id` would hit on the write
  request and miss on every request after -- which reads as "the filter does not stick" and is
  invisible to a single-request test. Every key here is `str()`.
- **Mutating a nested dict in place does not save it.** `session[KEY][exp] = x` leaves
  `session.modified` False and the write is lost with no error. These helpers always rebuild and
  reassign the whole top-level dict.
- **One key, not one per experiment.** `SESSION_SAVE_EVERY_REQUEST` is True, so that row is
  rewritten on every request; a reader who has visited three hundred experiments would pay for all
  three hundred on every page load. Hence `MAX_REMEMBERED_EXPERIMENTS`.

`SESSION_EXPIRE_AT_BROWSER_CLOSE` is True, so the filter dies with the browser. `django_session`
*is* a table -- what is gone is a filter table, a shared value, and any survival past the session.
"""

import dataclasses
import logging
from typing import Optional, Tuple

from mutint_common.util import get_gene_list

logger = logging.getLogger(__name__)

#: Everything the reader has filtered, `{"<experiment_id>": {...}}`, under one session key.
SESSION_KEY = "mutint_view_filters"

#: How many experiments' filters to remember. Oldest evicted first. See the module docstring:
#: this bounds a row that is rewritten on every single request.
MAX_REMEMBERED_EXPERIMENTS = 20

#: Bumped if the stored shape ever changes, so `from_session_dict` can discard what it does not
#: recognize rather than guess. A session outlives a deploy.
_SESSION_FORMAT = 1

#: The names the form posts, the interop API accepts, and the URL carries. One spelling, so the
#: page and the API cannot disagree about what `min_freq=20` means.
MIN_PARAM = "min_freq"
MAX_PARAM = "max_freq"
GENES_PARAM = "ignore_genes"
PARAMS = (MIN_PARAM, MAX_PARAM, GENES_PARAM)


@dataclasses.dataclass(frozen=True)
class ViewFilter:
    """What one reader is hiding, on one experiment.

    Frozen so it can be compared, memoised on the request and passed down four call layers
    without anyone mutating it halfway.
    """

    min_freq: Optional[int] = None
    max_freq: Optional[int] = None
    genes: Tuple[str, ...] = ()

    @property
    def is_empty(self):
        """True when this hides nothing. Thanks to normalization there is no third state."""
        return self == EMPTY

    @property
    def genes_set(self):
        """The ignored genes as a set, which is what `gene_is_filtered` compares against."""
        return frozenset(self.genes)

    # ---- construction ------------------------------------------------------------------
    @classmethod
    def parse(cls, min_freq=None, max_freq=None, genes=None):
        """The only place values are coerced. Raises `ValueError` on anything unusable.

        Raising rather than ignoring, because the two callers want to do different things and
        both want to know: the interop API turns it into a 400, since an anonymous caller who
        sent `min_freq=abc` and got unfiltered results has a wrong answer dressed as a right
        one; the page catches it and re-renders saying so.
        """
        low = _percent(min_freq, MIN_PARAM)
        high = _percent(max_freq, MAX_PARAM)
        if low is not None and high is not None and low > high:
            raise ValueError("minimum frequency %d%% is above the maximum %d%%" % (low, high))
        # 0 and 100 are the ends of the scale, so they exclude nothing -- see the docstring.
        if low == 0:
            low = None
        if high == 100:
            high = None
        return cls(min_freq=low, max_freq=high, genes=_gene_tuple(genes))

    @classmethod
    def from_params(cls, params):
        """Parse from a `QueryDict` or dict -- the form's GET, or the interop API's."""
        return cls.parse(min_freq=params.get(MIN_PARAM),
                         max_freq=params.get(MAX_PARAM),
                         genes=params.get(GENES_PARAM))

    @classmethod
    def from_session_dict(cls, data):
        """The opposite of `parse`: **never raises.**

        A session outlives a deploy, so a stored shape this version does not understand must
        cost the reader their filter, not every page they load.
        """
        if not isinstance(data, dict) or data.get("v") != _SESSION_FORMAT:
            return EMPTY
        try:
            return cls.parse(min_freq=data.get("min"), max_freq=data.get("max"),
                             genes=data.get("genes"))
        except (ValueError, TypeError):
            logger.debug("discarding an unreadable stored view filter: %r", data)
            return EMPTY

    def to_session_dict(self):
        """JSON-native only -- a list, not a tuple or a set. See the module docstring."""
        return {"v": _SESSION_FORMAT, "min": self.min_freq, "max": self.max_freq,
                "genes": list(self.genes)}


#: The filter that hides nothing. `is_empty` is defined against it.
EMPTY = ViewFilter()


def _percent(value, name):
    """A whole percent 0-100, or None. `''` means "not set", which is what a cleared box posts."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a whole number of percent, not %r" % (name, value))
    if not 0 <= number <= 100:
        raise ValueError("%s must be between 0 and 100, not %d" % (name, number))
    return number


def _gene_tuple(genes):
    """Parsed, stripped, de-duplicated, order preserved.

    Order is kept so the summary sentence reads the way the reader typed it. Parsed by
    `get_gene_list`, which is what the stored column used and what a mutation's own `gene` column
    is split with -- so what counts as a gene name is decided in one place.
    """
    if genes is None:
        return ()
    if isinstance(genes, str):
        names = get_gene_list(genes)
    else:
        names = [str(name).strip() for name in genes]
    seen, ordered = set(), []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered)


# ---- the session ------------------------------------------------------------------------
def _stored(request):
    stored = request.session.get(SESSION_KEY)
    return stored if isinstance(stored, dict) else {}


def get_view_filter(request, experiment_id):
    """The filter this reader is looking through, for this experiment. Never None.

    Resolution, in order:

    1. any of the three parameters **present** in the query string -- parse them, and remember
       the result in the session;
    2. otherwise whatever the session holds;
    3. otherwise `EMPTY`.

    **Presence, not truthiness**, which is what makes clearing work: the clear link sends the
    parameters empty, so rule 1 fires, `parse` yields `EMPTY`, and the stored entry is deleted.

    Writing to the session on a GET is the unusual part, so: what is written is idempotent,
    carries no authority, is derived entirely from the URL the reader is already looking at, and
    is only ever a display preference. The session is a memo of the last URL rather than a second
    source of truth -- it exists because the sidebar's links between Compare, Fixed Mutations and
    Converged Mutations carry no filter parameters, and the filter has to follow the reader
    across them.

    The accepted cost: a GET write has no CSRF protection, so a third-party page could set a
    reader's display preference for one experiment. It can only ever *narrow* what is shown,
    never reveal anything, it is stated in the summary line under the table, and it is one click
    to clear.
    """
    cache = getattr(request, "_mutint_view_filter_cache", None)
    if cache is None:
        cache = request._mutint_view_filter_cache = {}
    key = str(experiment_id)
    if key in cache:
        return cache[key]

    if any(name in request.GET for name in PARAMS):
        try:
            view_filter = ViewFilter.from_params(request.GET)
        except ValueError:
            # The page re-renders with the controls showing what is actually in force. It has
            # nowhere to put an error message that the reader would not have to dismiss, and a
            # filter that silently does nothing is worse than one that visibly does nothing.
            view_filter = EMPTY
        set_view_filter(request, experiment_id, view_filter)
    else:
        view_filter = ViewFilter.from_session_dict(_stored(request).get(key))

    cache[key] = view_filter
    return view_filter


def set_view_filter(request, experiment_id, view_filter):
    """Remember this reader's filter for this experiment. An empty filter forgets it instead.

    Rebuilds and reassigns the whole top-level dict: assigning into the nested one leaves
    `session.modified` False and loses the write with no error.
    """
    key = str(experiment_id)
    stored = dict(_stored(request))
    stored.pop(key, None)          # so a re-write moves the entry to the end, for eviction
    if not view_filter.is_empty:
        stored[key] = view_filter.to_session_dict()
    while len(stored) > MAX_REMEMBERED_EXPERIMENTS:
        stored.pop(next(iter(stored)))
    request.session[SESSION_KEY] = stored
    cache = getattr(request, "_mutint_view_filter_cache", None)
    if cache is not None:
        cache[key] = view_filter


def clear_view_filter(request, experiment_id):
    """Forget this reader's filter for this experiment."""
    set_view_filter(request, experiment_id, EMPTY)

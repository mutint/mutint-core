# The registries

There are eight, all in `mutint_common`. Each is a module holding a list, a `register_*`
function an app calls from `AppConfig.ready()`, and a `get_*` function core calls when it
renders. That is the whole mechanism — there is no plugin base class, no manifest and no
entry-point scanning.

They are separate modules rather than one because **core's own apps use them too**. The
sidebar is rendered from `nav_registry` whether or not a plugin is installed; `import_registry`
holds core's three import types. A plugin is just another caller.

## Which one you want

| registry | you register | core does |
|---|---|---|
| [`plugin_registry`](../reference/plugin_registry.md) | URL patterns, export handlers, rename hooks | mounts your pages, offers your export type |
| [`rebuild_registry`](../reference/rebuild_registry.md) | a function that recomputes derived data | marks it stale when the mutations change, and runs it |
| [`nav_registry`](../reference/nav_registry.md) | a label and a URL | draws a sidebar entry |
| [`import_registry`](../reference/import_registry.md) | a file type and a handler | offers it on the Add Data page, routes drops to it |
| [`about_registry`](../reference/about_registry.md) | a template | gives your component a section on `/about` |
| [`example_registry`](../reference/example_registry.md) | a directory of data | loads it with `./mutint load_example` |
| [`context_registry`](../reference/context_registry.md) | a callable | adds to the experiment views' context |
| [`panel_registry`](../reference/panel_registry.md) | a template and a context callable | draws your panel on the experiment Overview |

`context_registry` and `panel_registry` are close enough together to be worth telling apart.
The first hands the experiment views extra *context*, which some template must already be
written to render — so it can enrich a page core draws, and cannot add a section to one. The
second hands over a **template as well**, which is what lets a component put content of its own
on `/stats` with no edit to core. `mutint-needle`, the needle plot, is a component that
registers a panel and nothing else at all: no URL, no nav entry, no model.

## Direction

Seven of the eight run one way: an app contributes something and core consumes it. Registration
is additive and core never calls back.

`rebuild_registry` is the exception and the only one that runs **both** ways.
(`panel_registry` looks like a second exception, because core calls your context callable while
rendering. It is not: that call is core consuming what you registered, in the request that
draws it -- nothing tells you an experiment has changed.)

- Core → you: when an experiment's mutations change, core marks your derived data stale and,
  unless you opted out, calls your rebuild function.
- You → core: your plugin can call `request_rebuild()` to say that something *it* changed has
  invalidated derived data, including core's.

That second direction is what makes it possible to have derived data at all without core
knowing what it is. See [Models and derived data](derived-data.md).

## Ordering

Two of the seven have explicit ordering, and the distinction is worth internalising because it
determines whether you get to ask for a position at all.

- **`import_registry` has `priority`**, because a reference genome must be established before
  mutations that are hash-checked against it. Order is correctness.
- **`rebuild_registry` has `priority`**, because the per-experiment filter defaults must exist
  before anything that counts mutations through them, and installation-wide totals must be
  counted after the per-experiment tables they aggregate.
- **Everything else is `INSTALLED_APPS` order**, and deliberately has no `order` parameter. A
  nav entry's position is cosmetic, and so is a panel's; to move one, move its app. Plugins load after every core
  app, so a plugin's nav entry lands at the end of its section, and plugins order among
  themselves by their order in the assembled project's `.gitmodules`.

## Registering is not free of obligations

Two things a registry cannot check for you:

- **A registered template that does not exist** drops to a bare heading with a logged warning,
  and a `url_name` that will not reverse makes its nav entry disappear. Both are deliberate:
  one plugin's typo must not take down a page that is mostly other components' content. The
  consequence is that a mistake here is quiet.
- **Registering derived data means promising to keep it correct.** If you take the
  `auto=False` route so that nothing rebuilds it behind your back, the page that reads it has
  to check `is_stale` itself — and a page that forgets is worse off than one that never
  registered, because now there is a staleness record nobody reads.

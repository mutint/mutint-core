"""Registry of sidebar navigation entries contributed by apps.

Apps call register_nav_item() from their AppConfig.ready(); mutint_common's
base.html renders whatever has been registered. This keeps mutint-core free of
any compile-time knowledge of optional plugins: a plugin's entries appear only
when the plugin is installed.
"""

_nav_items = []

MAIN_SECTION = 'main'
EXPERIMENT_SECTION = 'experiment'
#: Rendered after the experiment section, so its entries sit under the selected experiment's
#: pages rather than above them. About is here: it is about the installation, and it belongs
#: at the foot of the menu, under the things somebody came to use.
END_SECTION = 'end'


def register_nav_item(label, url=None, url_name=None, section=MAIN_SECTION):
    """Register a sidebar entry (called from AppConfig.ready()).

    Entries render in registration order: apps in INSTALLED_APPS order, and
    within an app in the order register_nav_item() is called. There is
    deliberately no ordering parameter -- to move an entry, move its app in
    INSTALLED_APPS.

    label     text shown in the sidebar
    url       literal path, e.g. '/about'; mutually exclusive with url_name
    url_name  URL pattern name, reversed at render time
    section   MAIN_SECTION, always shown; EXPERIMENT_SECTION, shown only
              when an experiment is selected and rendered with
              ?experiment_id=... appended; or END_SECTION, always shown,
              after the experiment section
    """
    if (url is None) == (url_name is None):
        raise ValueError("register_nav_item() needs exactly one of url or url_name")
    _nav_items.append({
        'label': label,
        'url': url,
        'url_name': url_name,
        'section': section,
    })


def get_nav_items(section=MAIN_SECTION):
    """Return [{'label', 'url'}, ...] for one section, in registration order.

    url_name entries are reversed here rather than at registration time: the
    URLconf is not loaded while AppConfig.ready() runs. An entry whose route is
    not installed is skipped rather than raising, so one bad entry cannot take
    down every page.
    """
    from django.urls import NoReverseMatch, reverse

    items = []
    for item in _nav_items:
        if item['section'] != section:
            continue
        url = item['url']
        if url is None:
            try:
                url = reverse(item['url_name'])
            except NoReverseMatch:
                continue
        items.append({'label': item['label'], 'url': url})
    return items

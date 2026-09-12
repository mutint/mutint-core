from django.apps import AppConfig


class UpdateConfig(AppConfig):
    """The `/update/` page: what is installed, and moving onto a newer version.

    No `register_nav_item`, and deliberately. `nav_registry` has no per-user visibility
    concept, so a registered entry renders for anonymous visitors and leads straight to a
    403 -- and this page is superusers-only, which is a narrower gate than any entry that
    registry can express. The link is written into base.html's account block instead, beside
    Django admin, under the `{% if user.is_superuser %}` that already governs it.

    Adding `visible_to=` to nav_registry for one entry would be a mechanism with a single
    producer, which is the reasoning mutint_jobs/apps.py already records for the same choice.
    """

    name = 'mutint_update'

"""The per-user preference store and its endpoint."""

import json

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase

from mutint_common import preferences
from mutint_common.models import UserPreference

URL = "/preferences/"


class StoreTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="a", email="a@e.com", is_active=True)
        self.other = User.objects.create(username="b", email="b@e.com", is_active=True)

    def test_set_get_overwrite_and_forget(self):
        preferences.set_preference(self.user, "matrix.columns", {"hidden": ["description"]})
        self.assertEqual({"hidden": ["description"]},
                         preferences.get_preference(self.user, "matrix.columns"))
        preferences.set_preference(self.user, "matrix.columns", {"hidden": []})
        self.assertEqual({"hidden": []}, preferences.get_preference(self.user, "matrix.columns"))
        self.assertEqual(1, UserPreference.objects.count(), "overwrite, not a second row")
        preferences.set_preference(self.user, "matrix.columns", None)
        self.assertIsNone(preferences.get_preference(self.user, "matrix.columns"))
        self.assertEqual("fallback", preferences.get_preference(self.user, "matrix.columns",
                                                                "fallback"))

    def test_one_persons_keys_are_invisible_to_another(self):
        preferences.set_preference(self.user, "matrix.columns", 1)
        self.assertIsNone(preferences.get_preference(self.other, "matrix.columns"))
        self.assertEqual({}, preferences.get_preferences(self.other, "matrix."))

    def test_a_prefix_selects_a_family_of_keys(self):
        preferences.set_preference(self.user, "matrix.columns", 1)
        preferences.set_preference(self.user, "matrix.samples.4", 2)
        preferences.set_preference(self.user, "other.thing", 3)
        self.assertEqual({"matrix.columns": 1, "matrix.samples.4": 2},
                         preferences.get_preferences(self.user, "matrix."))

    def test_anonymous_has_nothing(self):
        self.assertEqual("d", preferences.get_preference(AnonymousUser(), "k", "d"))
        self.assertEqual({}, preferences.get_preferences(AnonymousUser(), ""))

    def test_a_bad_key_or_an_oversize_value_is_refused(self):
        for key in ("", "has space", "a/b", "x" * 101, 12):
            with self.assertRaises(preferences.BadPreference, msg=repr(key)):
                preferences.set_preference(self.user, key, 1)
        with self.assertRaises(preferences.BadPreference):
            preferences.set_preference(self.user, "big", "x" * (preferences.MAX_VALUE_BYTES + 1))


class EndpointTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="a", email="a@e.com", is_active=True)

    def _post(self, body):
        return self.client.post(URL, data=json.dumps(body), content_type="application/json")

    def test_anonymous_is_told_to_sign_in(self):
        # The signal the page falls back to localStorage on. ALEdb is public.
        self.assertEqual(403, self.client.get(URL).status_code)
        self.assertEqual(403, self._post({"key": "k", "value": 1}).status_code)

    def test_round_trip(self):
        self.client.force_login(self.user)
        response = self._post({"key": "matrix.columns", "value": {"hidden": ["gene"]}})
        self.assertEqual(200, response.status_code)
        self.assertEqual({"key": "matrix.columns", "value": {"hidden": ["gene"]}},
                         response.json())
        listed = self.client.get(URL + "?prefix=matrix.").json()["preferences"]
        self.assertEqual({"matrix.columns": {"hidden": ["gene"]}}, listed)

    def test_null_forgets(self):
        self.client.force_login(self.user)
        self._post({"key": "k", "value": 1})
        self._post({"key": "k", "value": None})
        self.assertEqual({}, self.client.get(URL + "?prefix=k").json()["preferences"])

    def test_bad_requests_are_400(self):
        self.client.force_login(self.user)
        self.assertEqual(400, self.client.post(URL, data="not json",
                                               content_type="application/json").status_code)
        self.assertEqual(400, self._post({"value": 1}).status_code)
        self.assertEqual(400, self._post({"key": "no spaces allowed", "value": 1}).status_code)
        self.assertEqual(400, self._post({"key": "big",
                                          "value": "x" * 20000}).status_code)

"""
LoginRequiredMiddleware.

This middleware gates every page on the private deployment, so it is worth
pinning: both of the bugs it used to have -- an old-style class Django could not
instantiate, and is_authenticated called as a method -- would have taken the
whole site down rather than failing quietly.
"""

from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from mutint_common.middleware import LoginRequiredMiddleware

LOGIN_URL = '/accounts/login/'


def _sentinel_response(request):
    return HttpResponse('reached the view')


class LoginRequiredMiddlewareTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create(username="pphaneuf", password="x")

    def respond(self, path, user, **settings_overrides):
        request = self.factory.get(path)
        request.user = user
        with override_settings(LOGIN_URL=LOGIN_URL, **settings_overrides):
            middleware = LoginRequiredMiddleware(_sentinel_response)
            return middleware(request)

    def test_middleware_can_be_instantiated(self):
        # Django >= 2.0 calls Middleware(get_response); the old
        # MIDDLEWARE_CLASSES style raised TypeError here.
        self.assertIsInstance(LoginRequiredMiddleware(_sentinel_response),
                              LoginRequiredMiddleware)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.respond('/mutations/', AnonymousUser())
        self.assertEqual(302, response.status_code)
        self.assertEqual(LOGIN_URL, response['Location'])

    def test_authenticated_user_reaches_the_view(self):
        response = self.respond('/mutations/', self.user)
        self.assertEqual(200, response.status_code)
        self.assertEqual(b'reached the view', response.content)

    def test_login_page_itself_is_exempt(self):
        response = self.respond(LOGIN_URL, AnonymousUser())
        self.assertEqual(200, response.status_code)

    def test_login_subpaths_are_exempt(self):
        # The pattern is matched with re.match, so anything under the login URL
        # is reachable -- password reset confirmation links, for instance.
        response = self.respond(LOGIN_URL + 'reset/abc123/', AnonymousUser())
        self.assertEqual(200, response.status_code)

    def test_configured_exempt_urls_are_honoured(self):
        response = self.respond('/about', AnonymousUser(),
                                LOGIN_EXEMPT_URLS=[r'^about'])
        self.assertEqual(200, response.status_code)

    def test_paths_outside_the_exempt_list_still_redirect(self):
        response = self.respond('/mutations/', AnonymousUser(),
                                LOGIN_EXEMPT_URLS=[r'^about'])
        self.assertEqual(302, response.status_code)

    def test_exempt_patterns_are_not_substring_matches(self):
        # re.match anchors at the start, so an exemption for "about" must not
        # open up "/private/about".
        response = self.respond('/private/about', AnonymousUser(),
                                LOGIN_EXEMPT_URLS=[r'^about'])
        self.assertEqual(302, response.status_code)


class MissingAuthenticationMiddlewareTest(SimpleTestCase):

    def test_a_request_without_a_user_fails_loudly(self):
        request = RequestFactory().get('/mutations/')
        middleware = LoginRequiredMiddleware(_sentinel_response)
        with self.assertRaises(AssertionError):
            middleware(request)

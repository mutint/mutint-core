"""Automatic sign-in on a local install. See mutint_common/auto_login.py.

The runner turns MUTINT_AUTO_LOGIN off for the whole suite, so every class here turns it
back on for itself. The test client's REMOTE_ADDR is 127.0.0.1, which is the loopback the
middleware asks for.
"""

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings

from mutint_common import auto_login
from mutint_common.auto_login import AutoLoginMiddleware

PASSWORD = "correct horse battery"


def _sentinel(request):
    return HttpResponse("reached the view")


@override_settings(MUTINT_AUTO_LOGIN=True)
class LoneAdminTestCase(TestCase):

    def test_one_superuser_is_the_lone_admin(self):
        root = User.objects.create_superuser("admin", "a@e.com", PASSWORD)
        self.assertEqual(root, auto_login.lone_admin())

    def test_a_second_user_turns_it_off(self):
        User.objects.create_superuser("admin", "a@e.com", PASSWORD)
        User.objects.create_user("reader", "r@e.com", PASSWORD)
        self.assertIsNone(auto_login.lone_admin())

    def test_a_lone_ordinary_user_is_not_it(self):
        User.objects.create_user("reader", "r@e.com", PASSWORD)
        self.assertIsNone(auto_login.lone_admin())

    def test_a_lone_inactive_superuser_is_not_it(self):
        User.objects.create_superuser("admin", "a@e.com", PASSWORD, is_active=False)
        self.assertIsNone(auto_login.lone_admin())

    def test_the_name_is_not_checked(self):
        """Renaming `admin` must not switch the feature off; counting is the rule."""
        root = User.objects.create_superuser("jeff", "a@e.com", PASSWORD)
        self.assertEqual(root, auto_login.lone_admin())


@override_settings(MUTINT_AUTO_LOGIN=True)
class PageLoadTestCase(TestCase):

    def setUp(self):
        self.root = User.objects.create_superuser("admin", "a@e.com", PASSWORD)

    def test_an_anonymous_page_load_is_signed_in(self):
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.root, response.wsgi_request.user)

    def test_and_stays_signed_in_on_the_next_request(self):
        self.client.get("/")
        response = self.client.get("/project/")
        self.assertEqual(self.root, response.wsgi_request.user)

    def test_the_sidebar_names_the_user(self):
        html = self.client.get("/").content.decode()
        self.assertIn("User <b>admin</b>", html)
        self.assertNotIn(">Login<", html)

    def test_a_second_user_leaves_the_visitor_anonymous(self):
        User.objects.create_user("reader", "r@e.com", PASSWORD)
        response = self.client.get("/")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    @override_settings(MUTINT_AUTO_LOGIN=False)
    def test_the_setting_turns_it_off(self):
        response = self.client.get("/")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_only_from_this_machine(self):
        response = self.client.get("/", REMOTE_ADDR="10.0.0.5")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_the_login_page_is_left_alone(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_logging_out_sticks_for_the_session(self):
        self.client.get("/")
        self.client.post("/accounts/logout/")
        response = self.client.get("/")
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertIn(">Login<", response.content.decode())

    def test_but_a_new_browser_session_is_signed_in_again(self):
        self.client.get("/")
        self.client.post("/accounts/logout/")
        response = Client().get("/")
        self.assertEqual(self.root, response.wsgi_request.user)

    def test_signing_in_by_hand_still_works_after_logging_out(self):
        User.objects.create_user("reader", "r@e.com", PASSWORD)
        self.client.post("/accounts/logout/")
        response = self.client.post(
            "/accounts/login/", {"username": "reader", "password": PASSWORD})
        self.assertEqual(302, response.status_code)
        self.assertEqual("reader", self.client.get("/").wsgi_request.user.username)


@override_settings(MUTINT_AUTO_LOGIN=True)
class MiddlewareTestCase(TestCase):
    """The pieces the client cannot isolate: the method rule and the chain's order."""

    def setUp(self):
        self.root = User.objects.create_superuser("admin", "a@e.com", PASSWORD)
        self.factory = RequestFactory()

    def respond(self, request):
        request.user = AnonymousUser()
        request.session = {}
        AutoLoginMiddleware(_sentinel)(request)
        return request

    def test_a_post_is_not_signed_in(self):
        """`auth.login` rotates the CSRF token, and a POST signed in here would then fail its
        own CSRF check, which runs after this middleware's pre-phase."""
        with self.assertNumQueries(0):
            request = self.respond(self.factory.post("/project/create/"))
        self.assertFalse(request.user.is_authenticated)

    def test_a_request_without_a_user_is_a_configuration_error(self):
        with self.assertRaises(AssertionError):
            AutoLoginMiddleware(_sentinel)(self.factory.get("/"))

    def test_it_sits_directly_after_authentication(self):
        chain = settings.MIDDLEWARE
        auth = chain.index("django.contrib.auth.middleware.AuthenticationMiddleware")
        self.assertEqual("mutint_common.auto_login.AutoLoginMiddleware", chain[auth + 1])

    def test_the_logout_route_is_the_declining_view(self):
        from django.urls import resolve
        view = resolve("/accounts/logout/").func
        self.assertIs(auto_login.DecliningLogoutView, view.view_class)

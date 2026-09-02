"""The account pages: login, changing a password, and the sidebar block that reaches them.

None of this was tested. The login page had gone years rendering Bootstrap 4 class names that
exist in no stylesheet here -- inert, so the two inputs had no space between them -- and
swallowing every form error, so a wrong password re-rendered a blank form and said nothing. The
sidebar's "Change Password" pointed at Django admin's page, which is wrapped in `admin_view`
and therefore bounced every non-staff user to the admin login.

What is worth pinning, and why each one is here rather than left to a reader's eye:

  * the *shape* of the login markup, because "it looks like every other page" is a claim that
    rots silently and the classes that broke it are nameable;
  * that a bad password says so, which is the behaviour the old template lost;
  * that the password page is **ours** and not admin's, because admin ships templates at the
    names Django's views default to and would render its own in its own chrome, with no error;
  * that the hand-written `name=` attributes still match the Django form's fields, which
    nothing else in this repo guards even though every page here renders fields by hand.
"""

import re

from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.test import TestCase

PASSWORD = "correct horse battery"
NEW_PASSWORD = "a different long one"


def field_names(html):
    """Every form field the rendered page posts, minus the machinery."""
    names = set(re.findall(r'name="([a-zA-Z0-9_]+)"', html))
    return names - {"csrfmiddlewaretoken", "next"}


class LoginPageTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="reader", email="r@e.com", password=PASSWORD)

    def test_it_renders_our_template(self):
        response = self.client.get("/accounts/login/")

        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_a_wrong_password_says_so(self):
        """The old template rendered `{{ form }}` nowhere at all, so a failed login came back
        as a blank form with no message -- indistinguishable from a page that had reloaded
        itself."""
        response = self.client.post(
            "/accounts/login/", {"username": "reader", "password": "wrong"})

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Please enter a correct username and password")

    def test_a_good_password_signs_you_in(self):
        response = self.client.post(
            "/accounts/login/", {"username": "reader", "password": PASSWORD})

        self.assertEqual(302, response.status_code)
        self.assertEqual("/", response["Location"])

    def test_where_you_were_going_survives_the_login(self):
        """`next` beats the view's `next_page`, so a @login_required bounce comes back."""
        response = self.client.post(
            "/accounts/login/",
            {"username": "reader", "password": PASSWORD, "next": "/ale/projects/"})

        self.assertEqual(302, response.status_code)
        self.assertEqual("/ale/projects/", response["Location"])

    def test_each_input_is_in_its_own_form_group(self):
        """This is the fix for "no space between the text boxes": `form-group` is what carries
        Bootstrap 3's 15px, and the page had none."""
        html = self.client.get("/accounts/login/").content.decode()

        self.assertEqual(2, html.count('class="form-group"'))

    def test_none_of_the_bootstrap_4_vocabulary_comes_back(self):
        """Every one of these existed only on this page, in no stylesheet, doing nothing. The
        button is the same `btn btn-primary` as every other affirmative action in the product;
        `btn-lg btn-block` was unique to this page."""
        html = self.client.get("/accounts/login/").content.decode()

        for dead in ("form-signin", "form-label-group", "align-content-lg-center",
                     "sr-only", "btn-lg", "btn-block", "col-sm-2"):
            self.assertNotIn(dead, html, "%s is back" % dead)
        self.assertIn('class="btn btn-primary"', html)

    def test_the_username_survives_a_failed_attempt(self):
        response = self.client.post(
            "/accounts/login/", {"username": "reader", "password": "wrong"})

        self.assertContains(response, 'value="reader"')


class PasswordChangePageTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="reader", email="r@e.com", password=PASSWORD)
        self.client.force_login(self.user)

    def test_signing_in_is_required(self):
        """`PasswordChangeView.dispatch` is `@login_required` in its own right, which matters
        because `LoginRequiredMiddleware` is only installed in the private settings."""
        self.client.logout()

        response = self.client.get("/accounts/password/")

        self.assertEqual(302, response.status_code)
        self.assertIn("/accounts/login/", response["Location"])

    def test_it_renders_our_page_and_not_django_admins(self):
        """Django's `PasswordChangeView` defaults to `registration/password_change_form.html`,
        which `django.contrib.admin` ships and, being first in INSTALLED_APPS, wins. Naming
        ours `accounts/...` is what avoids that -- and the failure it avoids is a page that
        renders perfectly, in admin's chrome, with nothing raised anywhere."""
        response = self.client.get("/accounts/password/")

        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, "accounts/password_change.html")
        self.assertTemplateNotUsed(response, "registration/password_change_form.html")

    def test_it_states_the_rules_before_refusing_you_by_them(self):
        """`AUTH_PASSWORD_VALIDATORS` is configured here with the length minimum raised to 9,
        and `new_password1.help_text` is the only place those rules are written down for a
        reader. Hand-rendering the input is what would drop it, and it needs `|safe` or the
        <ul> Django builds arrives as literal tags."""
        html = self.client.get("/accounts/password/").content.decode()

        self.assertIn("9 characters", html)
        self.assertNotIn("&lt;ul", html)

    def test_changing_it_lands_on_the_done_page(self):
        """`follow=True` deliberately: `PasswordChangeView.success_url` defaults to an
        *un-namespaced* `reverse_lazy("password_change_done")`, and these routes live under the
        `accounts` namespace -- so the default raises NoReverseMatch *after* the new password
        has been saved. A test asserting only that a 302 came back would pass through that."""
        response = self.client.post("/accounts/password/", {
            "old_password": PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        }, follow=True)

        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, "accounts/password_change_done.html")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_changing_it_does_not_sign_you_out(self):
        """What `update_session_auth_hash` is for, and the part a hand-rolled version gets
        wrong: rotating the password invalidates the session it was rotated from unless the
        view says otherwise."""
        self.client.post("/accounts/password/", {
            "old_password": PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        })

        response = self.client.get("/accounts/password/")

        self.assertEqual(200, response.status_code, "changing it logged the user out")

    def test_the_wrong_current_password_is_refused_and_says_so(self):
        response = self.client.post("/accounts/password/", {
            "old_password": "not it",
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        })

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Your old password was entered incorrectly")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_two_different_new_passwords_are_refused(self):
        response = self.client.post("/accounts/password/", {
            "old_password": PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD + " not",
        })

        self.assertContains(response, "didn", status_code=200)   # "didn’t match", curly quote
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_password_the_validators_reject_is_refused(self):
        """The four validators reach this page for free, through
        `PasswordChangeForm.clean_new_password2`. Nothing here implements them."""
        response = self.client.post("/accounts/password/", {
            "old_password": PASSWORD,
            "new_password1": "1234",
            "new_password2": "1234",
        })

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "too short")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))


class HandWrittenFieldsTestCase(TestCase):
    """The inputs are written out by hand, so nothing but this checks they are the right ones.

    Every form in this repo is hand-written markup rather than a rendered Django form, which is
    a deliberate house style and has one hole: a field renamed upstream, or an input deleted by
    accident, produces a form that silently fails validation forever -- "this field is
    required" about a box that is on the screen.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="reader", email="r@e.com", password=PASSWORD)

    def test_the_login_page_posts_what_the_form_expects(self):
        html = self.client.get("/accounts/login/").content.decode()

        self.assertEqual(set(AuthenticationForm().fields), field_names(html))

    def test_the_password_page_posts_what_the_form_expects(self):
        self.client.force_login(self.user)

        html = self.client.get("/accounts/password/").content.decode()

        self.assertEqual(set(PasswordChangeForm(self.user).fields), field_names(html))


class AuthAppParityTestCase(TestCase):
    """Both auth apps serve the same routes.

    `aledb_accounts` is installed in no settings module in this repo, so nothing else exercises
    it at all -- which is how it came to pass `next_page` as `re_path`'s extra-kwargs dict
    (a `TypeError` on every login) and to ship no login template, both unnoticed. They share
    one list now, and this is what says so.
    """

    def test_the_two_auth_apps_serve_the_same_routes(self):
        from aledb_accounts import urls as production
        from aledb_accounts_noauth import urls as default

        names = {p.name for p in default.urlpatterns}
        self.assertEqual(names, {p.name for p in production.urlpatterns})
        self.assertEqual(
            {"login", "logout", "password_change", "password_change_done"}, names)

    def test_neither_declares_a_different_namespace(self):
        from aledb_accounts import urls as production
        from aledb_accounts_noauth import urls as default

        self.assertEqual("accounts", default.app_name)
        self.assertEqual("accounts", production.app_name)


class SidebarAccountBlockTestCase(TestCase):
    """Who is offered which door.

    Rendered through a real page rather than by inspecting the template, because the gate is a
    template condition and the thing worth asserting is what a given person receives.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="reader", email="r@e.com", password=PASSWORD)
        self.staff = User.objects.create_user(
            username="staffer", email="s@e.com", password=PASSWORD, is_staff=True)
        self.superuser = User.objects.create_superuser(
            username="root", email="a@e.com", password=PASSWORD)

    def sidebar(self, user):
        self.client.force_login(user)
        return self.client.get("/accounts/password/").content.decode()

    def test_a_superuser_is_offered_the_django_admin(self):
        self.assertIn("Django admin", self.sidebar(self.superuser))

    def test_an_ordinary_user_is_not(self):
        self.assertNotIn("Django admin", self.sidebar(self.user))

    def test_a_staff_user_is_not_either(self):
        """Django's admin would admit them, and `load_projects` marks every imported user
        staff -- so a staff gate here would offer the link to nearly everyone on a real
        deployment. This is the assertion that keeps the gate at is_superuser."""
        self.assertNotIn("Django admin", self.sidebar(self.staff))

    def test_change_password_is_local_for_everyone_signed_in(self):
        html = self.sidebar(self.user)

        self.assertIn("Change Password", html)
        self.assertIn("/accounts/password/", html)
        self.assertNotIn("/admin/password_change/", html)

    def test_the_account_block_is_balanced_markup(self):
        """The username's <li> was left open and a stray </li> closed it two entries later; a
        new entry added to that block would have inherited it."""
        html = self.sidebar(self.superuser)
        menu = html[html.index('id="side-menu"'):]
        menu = menu[:menu.index("</ul>")]

        self.assertEqual(menu.count("<li"), menu.count("</li>"))

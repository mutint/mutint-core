"""The account pages: login, changing a password, and the sidebar block that reaches them.

None of this was tested. The login page had gone years rendering Bootstrap 4 class names that
exist in no stylesheet here -- inert, so the two inputs had no space between them -- and
swallowing every form error, so a wrong password re-rendered a blank form and said nothing. The
sidebar's "Change Password" pointed at Django admin's page, which is wrapped in `admin_view`
and therefore bounced every non-staff user to the admin login.

What is worth pinning, and why each one is here rather than left to a reader's eye:

  * the *shape* of the login markup, because "it looks like every other page" is a claim that
    rots silently and the classes that broke it are nameable;
  * that a bad password says so, which is the behavior the old template lost;
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
            {"username": "reader", "password": PASSWORD, "next": "/project/"})

        self.assertEqual(302, response.status_code)
        self.assertEqual("/project/", response["Location"])

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


class AuthSlotTestCase(TestCase):
    """The auth slot still resolves, with one occupant.

    This was `AuthAppParityTestCase`, which asserted that two apps served the same routes.
    There is one app now, and it took the plain name `mutint_accounts` (it was
    `mutint_accounts_noauth`): the original `mutint_accounts` existed to be the occupant
    carrying django-defender's brute-force protection, and with defender gone it was
    byte-for-byte the same behavior as the default -- an alternative that was not an
    alternative.

    What the parity test was really guarding is still guarded, and by construction rather than
    by assertion: both apps had drifted into separate bugs (a `next_page` passed as `re_path`'s
    extra-kwargs dict, raising `TypeError` on every login; and no login template at all)
    because each hand-wrote its own URLconf. They were consolidated onto
    `mutint_common.account_urls` long before this, which is what made the second app redundant.

    So what is left to check is the mechanism: an app declaring `auth_app = True` is what
    `get_core_urlpatterns` mounts at `/accounts/`, and a deployment swapping in its own is
    still how authentication is replaced.
    """

    def test_the_installed_auth_app_declares_the_slot(self):
        from django.apps import apps as django_apps

        slots = [cfg for cfg in django_apps.get_app_configs()
                 if getattr(cfg, "auth_app", False)]

        self.assertEqual(1, len(slots), "the slot takes the first match, so two is ambiguous")
        self.assertEqual("mutint_accounts", slots[0].name)

    def test_it_serves_the_four_routes_under_one_namespace(self):
        from mutint_accounts import urls as installed

        self.assertEqual("accounts", installed.app_name)
        self.assertEqual({"login", "logout", "password_change", "password_change_done"},
                         {p.name for p in installed.urlpatterns})

    def test_those_routes_are_what_is_actually_mounted(self):
        """Through the resolver, not the module: the slot is only worth anything if what it
        declares is what `/accounts/` reaches."""
        from django.urls import reverse

        self.assertEqual("/accounts/login/", reverse("accounts:login"))
        self.assertEqual("/accounts/logout/", reverse("accounts:logout"))
        self.assertEqual("/accounts/password/", reverse("accounts:password_change"))


class LogoutTestCase(TestCase):
    """Signing out posts, and the sidebar control has to be the thing that posts.

    `LogoutView` has been POST-only since Django 5.0, where a GET gets 405. The sidebar
    carried a plain `<a href>` for that entire period on 4.2, which worked -- so the upgrade
    turned a working control into a dead one, and nothing here would have noticed: no test
    signed out through the page, only through `self.client.logout()`, which calls the test
    client rather than the product.
    """

    def setUp(self):
        self.user = User.objects.create_user("someone", password="a-long-enough-one")
        self.client.force_login(self.user)

    def test_posting_signs_you_out(self):
        response = self.client.post("/accounts/logout/")
        self.assertEqual(302, response.status_code)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_get_is_refused(self):
        """Pinned because it is the reason the markup changed. If some future Django accepts
        a GET again, the form is still right and this test is what says the constraint moved."""
        self.assertEqual(405, self.client.get("/accounts/logout/").status_code)

    def test_the_sidebar_posts_rather_than_linking(self):
        """The markup, because a form that renders as an anchor again would 405 on click and
        the page would look entirely correct until somebody tried to leave."""
        body = self.client.get("/project/").content.decode()

        form = re.search(r'<form[^>]*action="/accounts/logout/"[^>]*>(.*?)</form>',
                         body, re.S)
        self.assertIsNotNone(form, "the sidebar's Logout is not a posting form")
        self.assertIn('method="post"', form.group(0))
        self.assertIn("csrfmiddlewaretoken", form.group(1))
        self.assertNotIn('<a href="/accounts/logout/"', body)


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
        """Django's admin would admit them, and the retired `load_projects` importer marked every user
        staff -- so a staff gate here would offer the link to nearly everyone on a real
        deployment. This is the assertion that keeps the gate at is_superuser."""
        self.assertNotIn("Django admin", self.sidebar(self.staff))

    def test_change_password_is_local_for_everyone_signed_in(self):
        html = self.sidebar(self.user)

        self.assertIn("Change Password", html)
        self.assertIn("/accounts/password/", html)
        self.assertNotIn("/admin/password_change/", html)

    def test_jobs_and_groups_are_account_entries(self):
        """Both are about *you*: the jobs you asked for, the groups you belong to.

        Neither is registered with `nav_registry`, which has no notion of an entry only some
        people see -- a registered one renders for an anonymous visitor and leads to a 403 or
        a login page. Groups was such an entry, sitting in MAIN_SECTION among Projects and
        Experiments as though it were data.
        """
        html = self.sidebar(self.user)

        self.assertIn("/group/", html)
        self.assertIn("Jobs", html)

    def test_an_anonymous_visitor_is_offered_none_of_them(self):
        """The other half of the reason they are not nav entries."""
        html = self.client.get("/accounts/login/").content.decode()

        self.assertNotIn("/group/", html)
        self.assertNotIn("/jobs/", html)
        self.assertNotIn("Change Password", html)

    def test_the_entries_are_a_submenu_of_the_username(self):
        """metisMenu collapses a nested <ul> and binds the <a> beside it -- see base.html.

        Asserted on the shape rather than on the collapsing, which is the plugin's and is
        already initialized on `#side-menu` by sb-admin-2. What can break here is the markup:
        move the <ul> out of that <li>, or into a second one, and the theme's mechanism
        silently stops applying while every link still renders.
        """
        html = self.sidebar(self.user)
        block = html[html.index("side-menu"):]
        block = block[block.index("reader"):]

        self.assertLess(block.index('class="nav nav-second-level"'), block.index("</li>"))

    def test_the_sidebar_is_balanced_markup(self):
        """The username's <li> was left open and a stray </li> closed it two entries later; a
        new entry added to that block would have inherited it.

        It counts to the <ul> that *matches* `#side-menu` rather than to the first `</ul>`
        after it, which the account submenu now closes first -- the older form of this test
        stopped at the submenu and read the outer <li> as unclosed.
        """
        html = self.sidebar(self.superuser)
        # From the opening `<ul` itself, not from the id attribute inside it, or the first
        # token seen is a `</ul>` and the depth starts at -1.
        menu = html[html.rindex("<ul", 0, html.index('id="side-menu"')):]

        depth, end = 0, None
        for index, token in _tags(menu):
            depth += 1 if token == "<ul" else -1
            if depth == 0:
                end = index
                break
        self.assertIsNotNone(end, "#side-menu is never closed")

        menu = menu[:end]
        self.assertEqual(menu.count("<li"), menu.count("</li>"))
        self.assertEqual(menu.count("<ul"), menu.count("</ul>") + 1)


def _tags(html):
    """(index, "<ul"|"</ul>") for each in document order."""
    found = []
    for token in ("<ul", "</ul>"):
        start = 0
        while True:
            at = html.find(token, start)
            if at == -1:
                break
            found.append((at, token))
            start = at + 1
    # "</ul>" also matches "<ul" at the opening angle of nothing, but "<ul" cannot match
    # inside "</ul>" -- the slash sits between -- so the two lists are disjoint.
    return sorted(found)

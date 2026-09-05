"""Serving breseq's HTML report: containment, and the sandbox that makes it safe.

This is the only HTML in the product MutInt did not write -- breseq generates it from the
sample's name, its read filenames and the reference's gene names, all of which a user
supplied. So most of what is tested here is the sandbox, and the one assertion that matters
most is that the header and the iframe attribute carry the *same* flags: they intersect rather
than combine, so a header narrower than the attribute silently disables what the attribute
allowed, and the symptom is "the evidence links stopped working" with nothing naming why.
"""

import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_experiment.models import Project
from mutint_sample.models import Sample
from mutint_sample.views import report as report_views


class ReportTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.owner)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.owner)

        from mutint_experiment.models import Population
        population = Population.objects.create(experiment=self.experiment, name="1")
        self.sample = Sample.objects.create(population=population, time_point=1, name="1",
                                            source_name="s1", report_stored=True)
        self._write_report()

    def _write_report(self, extra=()):
        root = store.ensure_dir(store.sample_report_dir(self.sample.pk))
        files = {
            "index.html": "<html><body>breseq index</body></html>",
            "summary.html": "<html><body>summary statistics</body></html>",
            "marginal.html": "<html><body>marginal</body></html>",
            "evidence.html": "<html><body>evidence viewer</body></html>",
            "log.txt": "the command line",
        }
        files.update(dict(extra))
        for name, body in files.items():
            path = os.path.join(root, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(body)
        return root

    def _url(self, path, sample=None, token=None):
        sample = sample or self.sample
        if token is None:
            token = report_views.sign_sample(sample.pk)
        return "/mutations/report/%d/files/%s/%s" % (sample.pk, token, path)

    def _files(self, path, sample=None, token=None, **headers):
        return self.client.get(self._url(path, sample=sample, token=token), **headers)

    # --- the sandbox -----------------------------------------------------------------------

    def test_the_header_and_the_attribute_carry_the_same_flags(self):
        """The assertion this module exists for.

        A browser *intersects* an iframe's `sandbox` attribute with a `CSP: sandbox` header,
        so a header missing a flag the attribute grants silently removes it. Nothing warns,
        and what a person sees is that links inside an evidence page do nothing.
        """
        page = self.client.get("/mutations/report/%d/" % self.sample.pk)
        rendered = page.content.decode()

        served = self._files("index.html")
        header = served["Content-Security-Policy"]

        self.assertTrue(header.startswith("sandbox "), header)
        header_flags = set(header[len("sandbox "):].split())
        attribute_flags = set(report_views.SANDBOX_FLAGS.split())

        self.assertEqual(attribute_flags, header_flags)
        self.assertIn('sandbox="%s"' % report_views.SANDBOX_FLAGS, rendered)

    def test_allow_same_origin_is_never_granted(self):
        """The one flag that would undo the whole thing.

        With `allow-same-origin` the frame is same-origin with us and its scripts can read our
        DOM, our cookies and the CSRF token -- which is the entire risk this design exists to
        answer, so it is worth a test of its own rather than being implied by the one above.
        """
        self.assertNotIn("allow-same-origin", report_views.SANDBOX_FLAGS)
        self.assertNotIn("allow-same-origin", self._files("index.html")["Content-Security-Policy"])

    def test_scripts_are_allowed_because_evidence_needs_them(self):
        # breseq 0.50's evidence.html is a JavaScript application: JSZip and pako inline, the
        # whole evidence tree as one base64 ZIP, unzipped client-side. Without scripts it is a
        # blank page.
        self.assertIn("allow-scripts", report_views.SANDBOX_FLAGS)

    def test_top_navigation_needs_a_user_gesture(self):
        # Evidence pages carry `<base target="_top">`, so their links navigate the top window;
        # without the flag every one of them silently does nothing. "By user activation" is
        # what stops a script redirecting the page on its own.
        self.assertIn("allow-top-navigation-by-user-activation", report_views.SANDBOX_FLAGS)
        self.assertNotIn("allow-top-navigation ", " %s " % report_views.SANDBOX_FLAGS)

    # --- staying inside the viewer -------------------------------------------------------

    def test_a_page_navigated_to_as_the_document_goes_back_into_the_viewer(self):
        """`_top` inside our frame is MutInt's window.

        So a click on *summary* from an evidence page replaced the whole page with the bare
        file. The browser says what it is loading, and a top-level navigation to a page of
        the report is answered with the viewer on that page.
        """
        response = self._files("summary.html", HTTP_SEC_FETCH_DEST="document")
        self.assertEqual(302, response.status_code)
        self.assertEqual("/mutations/report/%d/?page=summary.html" % self.sample.pk,
                         response["Location"])

    def test_the_frames_own_loads_are_served(self):
        response = self._files("summary.html", HTTP_SEC_FETCH_DEST="iframe")
        self.assertEqual(200, response.status_code)
        self.assertIn("summary statistics", b"".join(response.streaming_content).decode())

    def test_a_browser_that_says_nothing_is_served(self):
        # Too old for Sec-Fetch-Dest: it gets the file, sandboxed by the header as before.
        self.assertEqual(200, self._files("summary.html").status_code)

    def test_a_download_is_not_redirected(self):
        # A click on output.gd or log.txt is a top-level navigation too, and it wants the file.
        self._write_report(extra=[("output.gd", "#=GENOME_DIFF 1.0")])
        for path in ("output.gd", "log.txt"):
            response = self._files(path, HTTP_SEC_FETCH_DEST="document")
            self.assertEqual(200, response.status_code, path)

    def test_a_nested_evidence_page_redirects_to_its_own_path(self):
        self._write_report(extra=[("evidence/RA_12.html", "<html>pileup</html>")])
        response = self._files("evidence/RA_12.html", HTTP_SEC_FETCH_DEST="document")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response["Location"].endswith("?page=evidence/RA_12.html"))

    def test_the_viewer_frames_any_page_the_report_holds(self):
        # evidence.html is where the redirect lands most often; the fragment that names the
        # actual evidence page never reaches the server, so the client-side script hands it on.
        response = self.client.get("/mutations/report/%d/?page=evidence.html" % self.sample.pk)
        rendered = response.content.decode()
        self.assertIn("/evidence.html\"", rendered)
        self.assertIn("window.location.hash", rendered)

    def test_the_viewer_refuses_a_page_the_report_does_not_hold(self):
        response = self.client.get("/mutations/report/%d/?page=missing.html" % self.sample.pk)
        self.assertIn("/index.html\"", response.content.decode())

    def test_open_in_new_tab_opens_the_viewer_not_the_file(self):
        response = self.client.get("/mutations/report/%d/?page=summary.html" % self.sample.pk)
        rendered = response.content.decode()
        self.assertIn('href="/mutations/report/%d/?page=summary.html" target="_blank"'
                      % self.sample.pk, rendered)

    def test_every_file_is_sniff_proofed(self):
        for path in ("index.html", "log.txt"):
            self.assertEqual("nosniff", self._files(path)["X-Content-Type-Options"])

    def test_the_headers_survive_a_range_request(self):
        """serve_file has three exit points and the range branches are the easy ones to miss."""
        response = self.client.get(self._url("index.html"), HTTP_RANGE="bytes=0-4")
        self.assertEqual(206, response.status_code)
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertEqual("nosniff", response["X-Content-Type-Options"])

    # --- the capability URL ----------------------------------------------------------------

    def test_the_files_need_no_session(self):
        """The bug that made the report render once, with no images and dead links.

        A sandboxed frame has an *opaque* origin, so every request the report makes for
        itself is cross-site and Django's `SameSite=Lax` session cookie is not sent. The
        first load works because the parent initiates it; nothing after that does. So the
        files must be readable with no cookie at all -- which is what the signed URL buys.
        """
        self.client.logout()
        response = self._files("index.html")
        self.assertEqual(200, response.status_code)

    def test_a_forged_token_is_refused(self):
        self.assertEqual(404, self._files("index.html", token="not-a-token").status_code)
        self.assertEqual(
            404, self._files("index.html",
                             token=report_views.sign_sample(self.sample.pk) + "x").status_code)

    def test_a_token_for_another_sample_is_refused(self):
        # Minted honestly, but for something else -- the id in the path must match the id in
        # the signature, or one report's link would open another's.
        other = Sample.objects.create(population=self.sample.population, time_point=2,
                                      name="2", source_name="s2", report_stored=True)
        self.assertEqual(
            404, self._files("index.html", token=report_views.sign_sample(other.pk)).status_code)

    def test_an_expired_token_is_refused(self):
        from django.core import signing
        old = signing.dumps(self.sample.pk, salt=report_views.REPORT_SALT)
        with mock.patch.object(report_views, "REPORT_TOKEN_MAX_AGE", -1):
            self.assertEqual(404, self._files("index.html", token=old).status_code)

    def test_the_viewer_mints_a_working_token(self):
        page = self.client.get("/mutations/report/%d/" % self.sample.pk)
        import re
        match = re.search(r'src="(/mutations/report/\d+/files/[^"]+)"', page.content.decode())
        self.assertIsNotNone(match, "the viewer did not frame a report URL")

        self.client.logout()
        self.assertEqual(200, self.client.get(match.group(1)).status_code)

    def test_a_relative_link_from_the_report_keeps_the_token(self):
        """The reason the token is a path segment rather than a query parameter.

        breseq's pages link to `summary.html` and `breseq_icon.png` relative to themselves, so
        whatever authorises the request has to survive that resolution.
        """
        url = self._url("index.html")
        sibling = url.rsplit("/", 1)[0] + "/breseq_icon.png"
        self.client.logout()
        # No icon in the fixture, but the point is that it reaches the view rather than 404ing
        # on authorisation -- so a missing file is the only reason it can fail.
        self.assertEqual(404, self.client.get(sibling).status_code)
        self._write_report(extra={"breseq_icon.png": "not really a png"})
        self.assertEqual(200, self.client.get(sibling).status_code)

    # --- containment -----------------------------------------------------------------------

    def test_a_traversal_is_refused(self):
        outside = os.path.join(self.store, "secret.txt")
        with open(outside, "w") as handle:
            handle.write("not yours")

        for path in ("../secret.txt", "../../secret.txt", "a/../../secret.txt"):
            self.assertEqual(404, self._files(path).status_code, "served %r" % (path,))

    def test_a_symlink_out_of_the_report_is_refused(self):
        """breseq writes no symlinks, but the report is whatever was uploaded."""
        outside = os.path.join(self.store, "secret.txt")
        with open(outside, "w") as handle:
            handle.write("not yours")
        link = os.path.join(store.sample_report_dir(self.sample.pk), "escape.txt")
        os.symlink(outside, link)

        self.assertEqual(404, self._files("escape.txt").status_code)

    def test_a_nested_path_is_served(self):
        # Older breseq writes an evidence/ tree; the route takes a path so both layouts work.
        self._write_report(extra={os.path.join("evidence", "RA_1.html"): "<html>pileup</html>"})
        response = self._files("evidence/RA_1.html")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"pileup", b"".join(response.streaming_content))

    # --- who may see it --------------------------------------------------------------------

    def test_a_stranger_cannot_reach_the_viewer(self):
        """Where the permission check lives now: minting, not serving.

        The file route cannot ask who is calling -- it has no cookie to ask with -- so the
        viewer is the gate. A stranger never gets a token.
        """
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(
            404, self.client.get("/mutations/report/%d/" % self.sample.pk).status_code)

    def test_a_reader_may_see_it(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        from mutint_experiment.permissions import grant_project_access
        grant_project_access(self.project, reader, "read")
        self.client.force_login(reader)
        self.assertEqual(
            200, self.client.get("/mutations/report/%d/" % self.sample.pk).status_code)

    def test_a_sample_with_no_report_is_a_404(self):
        self.sample.report_stored = False
        self.sample.save(update_fields=["report_stored"])
        self.assertEqual(
            404, self.client.get("/mutations/report/%d/" % self.sample.pk).status_code)

    # --- the viewer ------------------------------------------------------------------------

    def test_the_viewer_frames_the_report(self):
        response = self.client.get("/mutations/report/%d/" % self.sample.pk)
        rendered = response.content.decode()

        self.assertEqual(200, response.status_code)
        self.assertIn('src="/mutations/report/%d/files/' % self.sample.pk, rendered)
        self.assertIn("/index.html\"", rendered)
        self.assertIn("<iframe", rendered)

    def test_the_bar_offers_the_pages_that_exist(self):
        response = self.client.get("/mutations/report/%d/" % self.sample.pk)
        rendered = response.content.decode()
        self.assertIn("summary.html", rendered)
        self.assertIn("marginal.html", rendered)

    def test_a_page_the_report_does_not_have_is_not_offered(self):
        os.remove(os.path.join(store.sample_report_dir(self.sample.pk), "marginal.html"))
        response = self.client.get("/mutations/report/%d/" % self.sample.pk)
        self.assertNotIn("marginal.html", response.content.decode())

    def test_an_unknown_page_falls_back_to_the_index(self):
        # `?page=` is not a second way into the files -- only what the bar offers.
        response = self.client.get(
            "/mutations/report/%d/?page=../../etc/passwd" % self.sample.pk)
        self.assertIn("/index.html\"", response.content.decode())
        self.assertNotIn("passwd", response.content.decode())

    def test_evidence_is_not_offered_on_its_own(self):
        # With no fragment it says "No evidence file specified in URL hash"; it is reached
        # from a row of the index, never opened directly.
        response = self.client.get("/mutations/report/%d/" % self.sample.pk)
        self.assertNotIn("?page=evidence.html", response.content.decode())


class LinkBarTestCase(TestCase):
    """The links on the per-sample mutation table."""

    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.owner)
        from mutint_experiment.views import _create_experiment
        from mutint_experiment.models import Population
        self.experiment = _create_experiment(self.project, "e", self.owner)
        population = Population.objects.create(experiment=self.experiment, name="1")
        self.sample = Sample.objects.create(population=population, time_point=1, name="1",
                                            source_name="s1")

    def _page(self):
        return self.client.get("/mutations/breseq?experiment_id=%s&sample_id=%s"
                               % (self.experiment.id, self.sample.pk))

    def test_the_links_appear_for_a_sample_with_a_report(self):
        self.sample.report_stored = True
        self.sample.save(update_fields=["report_stored"])
        rendered = self._page().content.decode()
        self.assertIn("/mutations/report/%d/" % self.sample.pk, rendered)
        self.assertIn("summary.html", rendered)

    def test_the_page_renders_without_one(self):
        # Most samples of an older deployment have no report: a .gd drop carries none.
        response = self._page()
        self.assertEqual(200, response.status_code)
        self.assertNotIn("/mutations/report/", response.content.decode())

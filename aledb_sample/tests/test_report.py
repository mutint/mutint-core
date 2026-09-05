"""Serving breseq's HTML report: containment, and the sandbox that makes it safe.

This is the only HTML in the product ALEdb did not write -- breseq generates it from the
sample's name, its read filenames and the reference's gene names, all of which a user
supplied. So most of what is tested here is the sandbox, and the one assertion that matters
most is that the header and the iframe attribute carry the *same* flags: they intersect rather
than combine, so a header narrower than the attribute silently disables what the attribute
allowed, and the symptom is "the evidence links stopped working" with nothing naming why.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from aledb_common import store
from aledb_experiment.models import Project
from aledb_sample.models import Sample
from aledb_sample.views import report as report_views


class ReportTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.owner)
        from aledb_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.owner)

        from aledb_experiment.models import Population
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

    def _files(self, path):
        return self.client.get("/mutations/report/%d/files/%s" % (self.sample.pk, path))

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

    def test_every_file_is_sniff_proofed(self):
        for path in ("index.html", "log.txt"):
            self.assertEqual("nosniff", self._files(path)["X-Content-Type-Options"])

    def test_the_headers_survive_a_range_request(self):
        """serve_file has three exit points and the range branches are the easy ones to miss."""
        response = self.client.get(
            "/mutations/report/%d/files/index.html" % self.sample.pk, HTTP_RANGE="bytes=0-4")
        self.assertEqual(206, response.status_code)
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertEqual("nosniff", response["X-Content-Type-Options"])

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

    def test_a_stranger_gets_404_not_403(self):
        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(404, self._files("index.html").status_code)
        self.assertEqual(
            404, self.client.get("/mutations/report/%d/" % self.sample.pk).status_code)

    def test_a_reader_may_see_it(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        from aledb_experiment.permissions import grant_project_access
        grant_project_access(self.project, reader, "read")
        self.client.force_login(reader)
        self.assertEqual(200, self._files("index.html").status_code)

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
        self.assertIn('src="/mutations/report/%d/files/index.html"' % self.sample.pk, rendered)
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
        self.assertIn("files/index.html", response.content.decode())

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
        patcher = override_settings(ALEDB_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.owner)
        from aledb_experiment.views import _create_experiment
        from aledb_experiment.models import Population
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

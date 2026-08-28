"""Guards against the documentation silently falling behind the registries.

Deliberately pure file reads. `mkdocs` and PyYAML are not installed in a normal environment --
the toolchain lives in `requirements-docs.txt`, which only `./aledb docs` installs -- so a test
that imported either would fail everywhere the docs are not being built, which is everywhere.

What these catch is a registry or a hook appearing with **no mention at all**, which is the
failure that actually happens: somebody adds `register_something` and the guide describing the
registries never learns about it. What they cannot catch is prose going out of date. That is
said out loud in `docs/contributing/docs.md` so the next person knows which half is watched.
"""

import ast
import io
import os
import re

from django.test import SimpleTestCase

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMMON_DIR = os.path.join(BASE_DIR, "aledb_common")
DOCS_DIR = os.path.join(BASE_DIR, "docs")


def registry_modules():
    return sorted(name[:-3] for name in os.listdir(COMMON_DIR)
                  if name.endswith("_registry.py"))


def documentation_text():
    """Every markdown page, concatenated."""
    chunks = []
    for root, _, files in os.walk(DOCS_DIR):
        for name in sorted(files):
            if name.endswith(".md"):
                chunks.append(io.open(os.path.join(root, name), encoding="utf-8").read())
    return "\n".join(chunks)


class ReferencePageTestCase(SimpleTestCase):

    def test_the_docs_directory_is_there(self):
        """Guards the guards: every assertion below passes vacuously against an empty tree."""
        self.assertTrue(os.path.isdir(DOCS_DIR), DOCS_DIR)
        self.assertTrue(registry_modules(), "no registries found to check")

    def test_every_registry_has_a_reference_page(self):
        for module in registry_modules():
            with self.subTest(module=module):
                path = os.path.join(DOCS_DIR, "reference", "%s.md" % module)
                self.assertTrue(
                    os.path.isfile(path),
                    "%s has no reference page. Add docs/reference/%s.md and list it in "
                    "mkdocs.yml." % (module, module))

    def test_every_reference_page_points_at_its_module(self):
        """A page that exists but names the wrong module renders somebody else's API under
        this one's heading, which is worse than having no page."""
        for module in registry_modules():
            with self.subTest(module=module):
                path = os.path.join(DOCS_DIR, "reference", "%s.md" % module)
                if not os.path.isfile(path):
                    continue          # reported by the test above
                text = io.open(path, encoding="utf-8").read()
                self.assertIn("::: aledb_common.%s" % module, text)

    def test_the_nav_lists_every_reference_page(self):
        """The nav is explicit precisely so an unlisted page is a visible omission; that only
        works if something checks."""
        nav = io.open(os.path.join(BASE_DIR, "mkdocs.yml"), encoding="utf-8").read()
        for module in registry_modules():
            with self.subTest(module=module):
                self.assertIn("reference/%s.md" % module, nav)


class RegisteredHookTestCase(SimpleTestCase):
    """Every way a plugin can announce itself has to be mentioned somewhere."""

    def public_register_functions(self):
        found = {}
        for module in registry_modules():
            source = io.open(os.path.join(COMMON_DIR, "%s.py" % module),
                             encoding="utf-8").read()
            for node in ast.walk(ast.parse(source)):
                if (isinstance(node, ast.FunctionDef)
                        and node.name.startswith("register_")):
                    found.setdefault(node.name, module)
        return found

    def test_there_are_some_to_check(self):
        self.assertGreater(len(self.public_register_functions()), 5)

    def test_every_registration_hook_is_documented(self):
        text = documentation_text()
        for name, module in sorted(self.public_register_functions().items()):
            with self.subTest(hook=name):
                # assertTrue, not assertIn: assertIn prints the haystack, and the haystack
                # here is every page in the site.
                self.assertTrue(
                    name in text,
                    "%s (in %s) is a way for a plugin to register something and is named "
                    "nowhere in docs/. Mention it in docs/plugin/registries.md or the guide "
                    "it belongs to." % (name, module))

class ProjectRootTestCase(SimpleTestCase):
    """Which project is being built, and which components contribute to it.

    `./aledb docs` is inherited by every assembled project -- both entry scripts end at
    `aledb_common.cli.manage()` -- so `./mutint docs` reaches the same command. It used to
    build the *submodule's* docs from there, because its base directory came from `__file__`.
    Now it builds the project's manual, and `ALEDB_TOOLS_DIR` is what tells it which project
    that is: the entry script exports it before re-execing and is the one place that knows.
    Settings cannot, because an assembled project reaches `get_base_settings()` through
    aledb-core's `config/defaults.py`.
    """

    def with_tools_dir(self, value):
        from aledb_common import docs_manual

        original = os.environ.get("ALEDB_TOOLS_DIR")
        if value is None:
            os.environ.pop("ALEDB_TOOLS_DIR", None)
        else:
            os.environ["ALEDB_TOOLS_DIR"] = value
        self.addCleanup(self._restore, original)
        return docs_manual.project_root()

    def _restore(self, original):
        if original is None:
            os.environ.pop("ALEDB_TOOLS_DIR", None)
        else:
            os.environ["ALEDB_TOOLS_DIR"] = original

    def test_the_project_root_is_two_levels_above_the_tools_dir(self):
        self.assertEqual("/somewhere/mutint",
                         self.with_tools_dir("/somewhere/mutint/env/tools"))

    def test_no_entry_script_means_no_answer(self):
        """Nothing exported it, so nothing ran through an entry script and there is nothing to
        compare against. The command falls back to aledb-core rather than guessing."""
        self.assertIsNone(self.with_tools_dir(None))

    def components_for(self, dirs, root):
        """`contributing_components` with a stand-in app registry."""
        from aledb_common import docs_manual

        class FakeApp:
            def __init__(self, path):
                self.path = path

        import aledb_common.about_registry as about

        first = about.first_party_app_configs
        component = about.component_dir
        # Each "app" sits one level inside its component, which is what component_dir undoes.
        about.first_party_app_configs = lambda: [
            FakeApp(os.path.join(d, "app")) for d in dirs]
        self.addCleanup(setattr, about, "first_party_app_configs", first)
        self.addCleanup(setattr, about, "component_dir", component)
        return docs_manual.contributing_components(root)

    def test_the_project_is_not_a_component_of_itself(self):
        """Standalone, aledb-core *is* the project and its apps are installed. Without this
        every page would appear twice -- once at the top level and once nested under a
        component heading."""
        found = self.components_for([BASE_DIR], BASE_DIR)

        self.assertEqual([], found)

    def test_a_component_with_docs_and_a_config_contributes(self):
        found = self.components_for([BASE_DIR], "/some/project")

        self.assertEqual([(os.path.basename(BASE_DIR), BASE_DIR)], found)

    def test_a_component_without_a_config_is_skipped(self):
        """Both files or neither. Most components will never have documentation, so a missing
        one is silence rather than a warning on every build."""
        found = self.components_for(["/nonexistent/aledb-yourthing"], "/some/project")

        self.assertEqual([], found)


class ManualAssemblyTestCase(SimpleTestCase):
    """Merging every installed component's pages into one manual, by audience.

    Pure dict manipulation, so none of this needs mkdocs or PyYAML installed -- the merge
    functions take configs as dicts and only `read_config`/`assemble` touch the filesystem.
    """

    def merge(self, project, components):
        from aledb_common import docs_manual

        # `read_config` is the only thing that would hit the disk, and these configs are
        # already dicts.
        original = docs_manual.read_config
        docs_manual.read_config = lambda directory: components[directory]
        self.addCleanup(setattr, docs_manual, "read_config", original)
        return docs_manual.merge_navs(
            project, [(os.path.basename(d), d) for d in components])

    def section(self, nav, title):
        for item in nav:
            if title in item:
                return item[title]
        return None

    def test_audiences_merge_across_components(self):
        """The point of the whole thing: a reader wants "how do I use this", not "everything
        aledb-fixation has to say"."""
        from aledb_common.docs_manual import EXTENDING, USING

        nav = self.merge(
            {"nav": [{USING: [{"Quick start": "using/quickstart.md"}]},
                     {EXTENDING: [{"Writing a plugin": "plugin/index.md"}]}]},
            {"aledb-fixation": {"nav": [{USING: [{"Fixed mutations": "using/fix.md"}]}]}})

        self.assertEqual([{"Quick start": "using/quickstart.md"},
                          {"Fixed mutations": "aledb-fixation/using/fix.md"}],
                         self.section(nav, USING))
        self.assertEqual([{"Writing a plugin": "plugin/index.md"}],
                         self.section(nav, EXTENDING))

    def test_a_components_paths_are_prefixed_and_the_projects_are_not(self):
        """The project's docs sit at the top of the build tree; a component's sit under its
        own directory name. Getting this backwards is a site of 404s."""
        from aledb_common.docs_manual import USING

        nav = self.merge(
            {"nav": [{USING: ["a.md"]}]},
            {"aledb-yourthing": {"nav": [{USING: ["b.md"]}]}})

        self.assertEqual(["a.md", "aledb-yourthing/b.md"], self.section(nav, USING))

    def test_a_components_own_ordering_survives(self):
        from aledb_common.docs_manual import USING

        nav = self.merge(
            {"nav": []},
            {"c": {"nav": [{USING: [{"Second": "b.md"}, {"First": "a.md"}]}]}})

        self.assertEqual([{"Second": "c/b.md"}, {"First": "c/a.md"}],
                         self.section(nav, USING))

    def test_nested_sections_are_prefixed_all_the_way_down(self):
        from aledb_common.docs_manual import EXTENDING

        nav = self.merge(
            {"nav": []},
            {"c": {"nav": [{EXTENDING: [{"Group": [{"Page": "deep/page.md"}]}]}]}})

        self.assertEqual([{"Group": [{"Page": "c/deep/page.md"}]}],
                         self.section(nav, EXTENDING))

    def test_an_unrecognised_heading_is_filed_not_dropped(self):
        """A component that has not thought about audience still builds, and its pages appear
        under its own name rather than vanishing. Silent loss is the failure worth avoiding."""
        from aledb_common.docs_manual import DEPLOYMENT

        nav = self.merge(
            {"nav": []},
            {"c": {"nav": [{"Some heading": ["x.md"]}]}})

        self.assertEqual([{"c": [{"Some heading": ["c/x.md"]}]}],
                         self.section(nav, DEPLOYMENT))

    def test_an_empty_section_is_omitted(self):
        """A heading with nothing under it is a promise the manual does not keep."""
        from aledb_common.docs_manual import EXTENDING, USING

        nav = self.merge({"nav": [{USING: ["a.md"]}]}, {})

        self.assertIsNotNone(self.section(nav, USING))
        self.assertIsNone(self.section(nav, EXTENDING))

    def test_the_sections_are_always_in_the_same_order(self):
        from aledb_common.docs_manual import AUDIENCES, DEPLOYMENT, EXTENDING, USING

        nav = self.merge(
            {"nav": [{DEPLOYMENT: ["d.md"]}, {EXTENDING: ["e.md"]}, {USING: ["u.md"]}]}, {})

        self.assertEqual([USING, EXTENDING, DEPLOYMENT], [list(i)[0] for i in nav])
        self.assertEqual(list(AUDIENCES), [list(i)[0] for i in nav])


class GeneratedConfigTestCase(SimpleTestCase):

    def config(self, components=()):
        from aledb_common import docs_manual

        original = docs_manual.read_config
        docs_manual.read_config = lambda directory: {"nav": []}
        self.addCleanup(setattr, docs_manual, "read_config", original)
        project = {"site_name": "MutInt", "nav": [],
                   "plugins": ["search", {"mkdocstrings": {"handlers": {"python": {}}}}]}
        return docs_manual.build_config("/p", project, list(components))

    def test_site_name_comes_from_the_project(self):
        self.assertEqual("MutInt", self.config()["site_name"])

    def test_every_component_is_on_the_mkdocstrings_path(self):
        """Or a plugin's `::: aledb_yourthing.util` does not resolve once its pages are being
        built from somewhere else."""
        config = self.config([("aledb-core", "/c"), ("aledb-fixation", "/f")])

        paths = config["plugins"][1]["mkdocstrings"]["handlers"]["python"]["paths"]
        self.assertEqual(["/p", "/c", "/f"], paths)

    def test_the_docs_dir_is_not_the_configs_own_directory(self):
        """mkdocs refuses that outright, which is how this was found."""
        self.assertEqual("docs", self.config()["docs_dir"])

    def test_component_sources_are_watched(self):
        """`mkdocs serve` watches docs_dir, which holds symlinks into repositories it would
        otherwise never look at."""
        config = self.config([("aledb-fixation", "/f")])

        self.assertIn(os.path.join("/f", "docs"), config["watch"])

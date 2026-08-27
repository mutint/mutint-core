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

    def test_the_input_vocabulary_is_documented(self):
        """`inputs=` is only usable if its two values are written down somewhere."""
        text = documentation_text()
        source = io.open(os.path.join(COMMON_DIR, "rebuild_registry.py"),
                         encoding="utf-8").read()
        for constant in re.findall(r"^(INPUT_[A-Z_]+)\s*=", source, re.M):
            with self.subTest(constant=constant):
                self.assertTrue(constant in text,
                                "%s is undocumented; `inputs=` is unusable without its "
                                "values written down." % constant)


class DocsCommandTestCase(SimpleTestCase):
    """`./aledb docs` is inherited by every assembled project, and must not build there.

    Both entry scripts end at `aledb_common.cli.manage()`, so `./mutint docs` reaches this
    command. Its `BASE_DIR` comes from `__file__`, which in that case is the *submodule* -- so
    without a guard it builds `mutint/aledb-core/site/` out of a detached-HEAD checkout: a
    second copy of a site whose sources are somewhere else, stale as soon as the pointer moves.

    `ALEDB_TOOLS_DIR` is what tells the two apart. The entry script exports it before
    re-execing and is the one place that knows the project root -- settings cannot, because an
    assembled project reaches `get_base_settings()` through aledb-core's `config/defaults.py`.
    """

    def command(self):
        from aledb_common.management.commands.docs import Command

        return Command()

    def refuse_with(self, tools_dir):
        """The guard alone, without invoking mkdocs."""
        from django.core.management import CommandError
        from django.test import override_settings  # noqa: F401  (kept for symmetry)

        original = os.environ.get("ALEDB_TOOLS_DIR")
        if tools_dir is None:
            os.environ.pop("ALEDB_TOOLS_DIR", None)
        else:
            os.environ["ALEDB_TOOLS_DIR"] = tools_dir
        self.addCleanup(self._restore, original)
        try:
            self.command()._refuse_if_embedded()
        except CommandError as error:
            return str(error)
        return None

    def _restore(self, original):
        if original is None:
            os.environ.pop("ALEDB_TOOLS_DIR", None)
        else:
            os.environ["ALEDB_TOOLS_DIR"] = original

    def test_it_builds_when_this_repo_is_the_project(self):
        """`./aledb docs` from aledb-core itself. The guard must not fire, or the command is
        useless where it is the whole point."""
        self.assertIsNone(self.refuse_with(os.path.join(BASE_DIR, "env", "tools")))

    def embedded_tools_dir(self):
        """A project root that really is an ancestor of this checkout, which is the shape an
        assembled project has: `mutint/env/tools` with `mutint/aledb-core/` inside it."""
        return os.path.join(os.path.dirname(BASE_DIR), "env", "tools")

    def test_it_refuses_when_this_repo_is_a_submodule(self):
        message = self.refuse_with(self.embedded_tools_dir())

        self.assertIsNotNone(message, "building into a submodule was allowed")
        self.assertIn("submodule", message)
        self.assertIn("./aledb docs", message, "it has to say what to run instead")

    def test_it_does_not_claim_a_detached_head_it_has_not_looked_at(self):
        """The submodule wording is only true when this checkout is actually inside the
        project. Shadowed on PYTHONPATH it is not, and a confident wrong sentence about git
        state is the kind that costs an afternoon."""
        embedded = self.refuse_with(self.embedded_tools_dir())
        elsewhere = self.refuse_with("/somewhere/else/env/tools")

        self.assertIn("detached-HEAD", embedded)
        self.assertIsNotNone(elsewhere, "a different checkout is still refused")
        self.assertNotIn("detached-HEAD", elsewhere)

    def test_an_unset_variable_is_not_enough_to_refuse_on(self):
        """Nothing exported it, so this was not run through an entry script and there is
        nothing to compare against. Refusing would be a guess."""
        self.assertIsNone(self.refuse_with(None))

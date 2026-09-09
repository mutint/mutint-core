"""Upgrading in place, tested without a remote.

`mutint_common/upgrade.py` is loaded by the entry script *before* the venv exists, so it
carries the same three constraints `pg.py` does and they are checked the same way: parseable
on 3.9, no Django import, standard library only. Those failures land on the machines that have
*not* upgraded yet, and never on the machine of whoever wrote the code.

The rest is the half that matters more than the happy path: what it refuses. This module runs
in the development checkouts of this suite, where applying an upgrade would be vandalism, so
every refusal gets a test against a real temporary repository rather than a mock -- `git
status --porcelain` and `rev-list @{upstream}..HEAD` are the things being trusted, and a mock
of them would only assert that the mock was written to agree.
"""

import ast
import json
import os
import shutil
import subprocess
import tempfile

from django.test import TestCase

from mutint_common import upgrade

SOURCE_PATH = upgrade.__file__.replace(".pyc", ".py")

#: Everything the standard library gives us. A name outside this set means the module grew a
#: dependency, which is the failure this whole file exists to catch early.
STDLIB = {"json", "os", "re", "subprocess", "datetime", "importlib"}


class ParseabilityTestCase(TestCase):
    """Constraints that hold before Python or Django are available."""

    def test_it_parses_on_python_39(self):
        with open(SOURCE_PATH) as handle:
            source = handle.read()

        ast.parse(source, feature_version=(3, 9))

    def test_it_imports_nothing_from_django(self):
        """Loaded before the venv exists. A Django import would fail on the first command
        anybody ran, not at some later and clearer moment."""
        with open(SOURCE_PATH) as handle:
            tree = ast.parse(handle.read())

        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        self.assertEqual([], [name for name in imported if name.split(".")[0] == "django"])

    def test_it_imports_only_the_standard_library(self):
        """`pg.py` gets away with checking Django alone because it imports four obvious
        modules. This one reaches for git, JSON and timestamps, so the temptation to import
        `requests` -- which *is* installed, just not yet -- is real."""
        with open(SOURCE_PATH) as handle:
            tree = ast.parse(handle.read())

        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])

        self.assertEqual(set(), roots - STDLIB)


class GitPathTestCase(TestCase):
    """`env/tools/bin` first, then PATH -- the order `mutint_common.tools.tool_path` uses."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def _managed(self):
        directory = os.path.join(self.base, "env", "tools", "bin")
        os.makedirs(directory)
        path = os.path.join(directory, "git")
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def test_the_managed_copy_wins(self):
        managed = self._managed()

        self.assertEqual(managed, upgrade.git_path(self.base))

    def test_it_falls_back_to_path(self):
        """Which is what makes an upgrade work on a checkout whose tools were never
        installed, and on a machine that has its own git."""
        found = upgrade.git_path(self.base)

        self.assertIsNotNone(found)
        self.assertNotIn(self.base, found)

    def test_a_non_executable_file_is_not_a_git(self):
        directory = os.path.join(self.base, "env", "tools", "bin")
        os.makedirs(directory)
        open(os.path.join(directory, "git"), "w").close()

        self.assertNotIn(self.base, upgrade.git_path(self.base) or "")


class StateTestCase(TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_a_missing_file_reads_as_the_stable_channel(self):
        """A new installation has no state, and must not need one seeded."""
        self.assertEqual(upgrade.STABLE, upgrade.read_state(self.base)["channel"])

    def test_a_corrupt_file_does_not_take_a_launch_down(self):
        """The entry script reads this before Django exists. A half-written file is worth a
        default, not a traceback in front of somebody starting MutInt."""
        os.makedirs(os.path.join(self.base, "data"))
        with open(upgrade.state_path(self.base), "w") as handle:
            handle.write("{not json")

        self.assertEqual(upgrade.STABLE, upgrade.read_state(self.base)["channel"])

    def test_it_round_trips(self):
        upgrade.write_state(self.base, {"channel": upgrade.MAIN, "checked_at": "now"})

        state = upgrade.read_state(self.base)
        self.assertEqual(upgrade.MAIN, state["channel"])
        self.assertEqual("now", state["checked_at"])

    def test_it_creates_the_data_directory(self):
        upgrade.write_state(self.base, {"channel": upgrade.STABLE})

        self.assertTrue(os.path.isfile(upgrade.state_path(self.base)))

    def test_a_request_is_recorded_and_cleared(self):
        upgrade.request(self.base, "v1.2.3", by="admin")
        self.assertEqual("v1.2.3", upgrade.read_state(self.base)["requested"]["ref"])

        upgrade.clear_request(self.base)
        self.assertNotIn("requested", upgrade.read_state(self.base))


class VersionOrderTestCase(TestCase):

    def test_tags_sort_numerically_not_lexically(self):
        """v0.10.0 is newer than v0.9.0, and a string sort says otherwise."""
        tags = ["v0.9.0", "v0.10.0", "v0.2.0"]

        self.assertEqual("v0.10.0", sorted(tags, key=upgrade._version_key)[-1])

    def test_a_shorter_tag_sorts_below_a_longer_one_sharing_its_prefix(self):
        self.assertLess(upgrade._version_key("v1.2"), upgrade._version_key("v1.2.1"))

    def test_only_plain_release_tags_match(self):
        for tag in ("v1", "v1.2", "v1.2.3"):
            self.assertIsNotNone(upgrade.TAG_RE.match(tag), tag)
        for tag in ("v1.2.0-rc1", "testdata-x-v1", "1.2.3", "v1.2.3+g9f8e7d"):
            self.assertIsNone(upgrade.TAG_RE.match(tag), tag)


def _run(base, *args):
    subprocess.run(["git", "-C", base] + list(args), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class BlockersTestCase(TestCase):
    """What stops an upgrade, against real repositories.

    These are the tests that protect a developer's working tree, so they exercise git itself
    rather than a mock of it -- the whole question is whether `git status --porcelain` says
    what this module thinks it says.
    """

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("no git available")
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def _repo(self):
        _run(self.base, "init", "-q")
        _run(self.base, "config", "user.email", "t@example.com")
        _run(self.base, "config", "user.name", "T")
        with open(os.path.join(self.base, "a.txt"), "w") as handle:
            handle.write("one\n")
        _run(self.base, "add", "a.txt")
        _run(self.base, "commit", "-qm", "one")

    def test_a_tree_with_no_git_says_how_to_adopt_it(self):
        problems = upgrade.blockers(self.base)

        self.assertEqual(1, len(problems))
        self.assertIn("--adopt", problems[0])

    def test_a_repository_with_no_origin_is_refused(self):
        self._repo()

        self.assertTrue(any("origin" in problem for problem in upgrade.blockers(self.base)))

    def test_uncommitted_work_is_refused_and_named(self):
        """The refusal that matters most: this same entry script runs in the development
        checkouts of this suite."""
        self._repo()
        with open(os.path.join(self.base, "a.txt"), "w") as handle:
            handle.write("edited\n")

        problems = upgrade.blockers(self.base)

        self.assertTrue(any("uncommitted" in problem for problem in problems))
        self.assertTrue(any("a.txt" in problem for problem in problems))

    def test_an_untracked_file_does_not_block(self):
        """The difference between this working and not. `data/` and `env/` are gitignored so
        they never appear here, but an export somebody downloaded into the directory does --
        and an installation that stops being upgradeable the first time a file is saved beside
        it is no installation at all. `git checkout` does not discard an untracked file
        either; it refuses and names it, which `apply` passes on."""
        self._repo()
        _run(self.base, "remote", "add", "origin", "https://example.invalid/x.git")
        with open(os.path.join(self.base, "new.txt"), "w") as handle:
            handle.write("x\n")

        self.assertEqual([], upgrade.blockers(self.base))

    def test_a_staged_file_blocks(self):
        """Tracked and modified, which is somebody working here."""
        self._repo()
        with open(os.path.join(self.base, "b.txt"), "w") as handle:
            handle.write("x\n")
        _run(self.base, "add", "b.txt")

        self.assertTrue(any("uncommitted" in p for p in upgrade.blockers(self.base)))

    def test_the_first_changed_file_keeps_its_whole_name(self):
        """Porcelain's status occupies two columns and a space, so stripping the output --
        rather than each line -- eats the first line's leading space and silently reports
        `.txt` instead of `a.txt`. It did exactly that once."""
        self._repo()
        with open(os.path.join(self.base, "a.txt"), "w") as handle:
            handle.write("edited\n")

        problems = [p for p in upgrade.blockers(self.base) if "uncommitted" in p]

        self.assertIn("(a.txt)", problems[0])

    def test_a_clean_repository_with_an_origin_has_nothing_against_it(self):
        self._repo()
        _run(self.base, "remote", "add", "origin", "https://example.invalid/x.git")

        self.assertEqual([], upgrade.blockers(self.base))


class AdoptTestCase(TestCase):
    """Turning a tree with no history into a checkout, without touching a file.

    The case is somebody who unpacked GitHub's source archive, or copied an installation
    without its `.git`. Both are recoverable and neither should cost the data sitting in the
    directory.
    """

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("no git available")
        self.origin = tempfile.mkdtemp()
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.origin, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

        _run(self.origin, "init", "-q")
        _run(self.origin, "config", "user.email", "t@example.com")
        _run(self.origin, "config", "user.name", "T")
        with open(os.path.join(self.origin, "code.txt"), "w") as handle:
            handle.write("released\n")
        _run(self.origin, "add", "-A")
        _run(self.origin, "commit", "-qm", "one")
        _run(self.origin, "tag", "-a", "v1.0.0", "-m", "v1.0.0")

    def _unpack(self, contents="released\n"):
        """A tree holding the tag's files and no history, as an archive gives you."""
        with open(os.path.join(self.base, "code.txt"), "w") as handle:
            handle.write(contents)

    def test_a_matching_tree_becomes_a_checkout(self):
        self._unpack()

        result = upgrade.adopt(self.base, self.origin, "v1.0.0")

        self.assertTrue(result["ok"])
        self.assertEqual("v1.0.0", upgrade.current_ref(self.base))
        self.assertEqual([], upgrade.blockers(self.base))

    def test_data_beside_it_is_untouched(self):
        """The whole point: adoption must not cost somebody their database."""
        self._unpack()
        os.makedirs(os.path.join(self.base, "data", "store"))
        precious = os.path.join(self.base, "data", "store", "keep.txt")
        with open(precious, "w") as handle:
            handle.write("irreplaceable\n")

        upgrade.adopt(self.base, self.origin, "v1.0.0")

        self.assertEqual("irreplaceable\n", open(precious).read())

    def test_untracked_files_do_not_stop_it(self):
        """An export somebody downloaded into the directory is not a reason to refuse."""
        self._unpack()
        with open(os.path.join(self.base, "notes.txt"), "w") as handle:
            handle.write("mine\n")

        self.assertTrue(upgrade.adopt(self.base, self.origin, "v1.0.0")["ok"])

    def test_a_tree_that_is_not_that_release_is_refused_intact(self):
        """A **mixed** reset is what makes this check real: nothing is written, so the difference
        still exists to be reported. A forced checkout would have destroyed it first and
        reported success."""
        self._unpack(contents="something else\n")

        with self.assertRaises(upgrade.UpgradeError) as caught:
            upgrade.adopt(self.base, self.origin, "v1.0.0")

        self.assertIn("does not match", str(caught.exception))
        self.assertIn("code.txt", str(caught.exception))
        self.assertEqual("something else\n",
                         open(os.path.join(self.base, "code.txt")).read())

    def test_an_existing_checkout_is_not_adopted(self):
        self._unpack()
        upgrade.adopt(self.base, self.origin, "v1.0.0")

        with self.assertRaises(upgrade.UpgradeError):
            upgrade.adopt(self.base, self.origin, "v1.0.0")


class ApplyStagedTestCase(TestCase):
    """The entry script's hook. Its contract is that it never takes a launch down."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_no_request_is_a_no_op(self):
        self.assertIsNone(upgrade.apply_staged(self.base))

    def test_a_failing_upgrade_is_recorded_rather_than_raised(self):
        """A launch that refuses to start because an upgrade failed leaves somebody with no
        MutInt at all, which is strictly worse than an old one plus a message."""
        upgrade.request(self.base, "v9.9.9")

        result = upgrade.apply_staged(self.base)

        self.assertFalse(result["ok"])
        self.assertIn("adopt", result["detail"])

    def test_the_request_is_cleared_even_when_it_failed(self):
        """Left in place, every launch from here on would retry the same failing upgrade."""
        upgrade.request(self.base, "v9.9.9")
        upgrade.apply_staged(self.base)

        state = upgrade.read_state(self.base)
        self.assertNotIn("requested", state)
        self.assertFalse(state["last_result"]["ok"])


class CheckTestCase(TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_it_records_why_it_could_not_check(self):
        """"Could not ask" and "nothing newer" must not look alike to the page -- a reader
        told they are up to date when nobody managed to look is worse than an error."""
        state = upgrade.check(self.base)

        self.assertIsNone(state["available"])
        self.assertIn("error", state)

    def test_it_stores_the_channel_it_was_given(self):
        state = upgrade.check(self.base, channel=upgrade.MAIN)

        self.assertEqual(upgrade.MAIN, state["channel"])
        self.assertEqual(upgrade.MAIN, upgrade.read_state(self.base)["channel"])


class SummarizeTestCase(TestCase):
    """The sentence the upgrade page shows.

    It said `main is available.` and nothing else -- not which commit, not when, and not what
    installing it would do to the components, which is most of what an upgrade *is*. A pure
    function over what `describe` managed to find out, so every field is optional.
    """

    def test_the_bare_case_is_what_it_always_said(self):
        """`describe` answers nothing when the fetch failed, and the version is still
        available and still installable."""
        self.assertEqual("main is available.", upgrade.summarize("main", "abc12345", {}))

    def test_it_names_the_commit_and_when_it_was_made(self):
        sentence = upgrade.summarize("main", "abc12345", {
            "sha": "def67890", "date": "2026-09-07T11:53:23-04:00", "commits": 12})

        self.assertIn("commit def67890", sentence)
        # The time, not just the day: `main` moves several times a day on a project being
        # worked on, and a date alone cannot say whether this is the commit you just pushed.
        self.assertIn("committed 2026-09-07 11:53 -04:00", sentence)
        self.assertIn("12 commits newer than abc12345", sentence)

    def test_one_commit_is_not_pluralised(self):
        self.assertIn("1 commit newer",
                      upgrade.summarize("main", "abc12345", {"commits": 1}))

    def test_it_says_when_no_component_moves(self):
        sentence = upgrade.summarize("main", "abc12345", {
            "sha": "def67890", "components": [], "component_total": 6})

        self.assertIn("No component changes", sentence)
        self.assertIn("6", sentence)

    def test_it_names_every_component_that_moves(self):
        sentence = upgrade.summarize("main", "abc12345", {
            "sha": "def67890", "component_total": 6, "components": [
                {"name": "mutint-core", "from": "1111aaaa", "to": "2222bbbb",
                 "change": "moved"},
                {"name": "mutint-needle", "from": None, "to": "3333cccc",
                 "change": "added"}]})

        self.assertIn("moves 2 of 6 components", sentence)
        self.assertIn("mutint-core (1111aaaa to 2222bbbb)", sentence)
        self.assertIn("mutint-needle (added)", sentence)

    def test_a_timestamp_of_another_shape_is_shown_as_it_came(self):
        """Rather than sliced into nonsense by a rule written for `%cI`."""
        self.assertIn("whenever", upgrade.summarize("main", None, {"date": "whenever"}))


class ComponentChangesTestCase(TestCase):
    """Which components an upgrade would move, read out of the target commit's own tree.

    Against a real repository with a real submodule: the question is entirely about what
    `git ls-tree` prints for a gitlink, which no mock of it would establish.
    """

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("no git available")
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.base = os.path.join(self.root, "super")
        self.child = os.path.join(self.root, "child")

        for path in (self.base, self.child):
            os.makedirs(path)
            _run(path, "init", "-q", "-b", "main")
            _run(path, "config", "user.email", "t@example.com")
            _run(path, "config", "user.name", "T")

        self._commit(self.child, "a.txt", "one")
        # Local file transport, as every submodule in this suite is added.
        _run(self.base, "-c", "protocol.file.allow=always",
             "submodule", "add", "-q", self.child, "child")
        _run(self.base, "commit", "-qm", "add child")

    def _commit(self, path, name, text):
        with open(os.path.join(path, name), "w") as handle:
            handle.write(text + "\n")
        _run(path, "add", name)
        _run(path, "commit", "-qm", text)

    def test_a_component_that_moved_is_reported_with_both_shas(self):
        before = upgrade._tree_components(self.base, "HEAD")["child"]
        self._commit(self.child, "a.txt", "two")
        _run(os.path.join(self.base, "child"), "fetch", "-q", "origin")
        _run(os.path.join(self.base, "child"), "checkout", "-q", "origin/main")
        _run(self.base, "commit", "-qam", "bump child")
        after = upgrade._tree_components(self.base, "HEAD")["child"]

        changes = upgrade.component_changes(self.base, "HEAD~1")

        self.assertEqual(1, len(changes))
        self.assertEqual("child", changes[0]["name"])
        self.assertEqual("moved", changes[0]["change"])
        self.assertEqual(after[:8], changes[0]["from"])
        self.assertEqual(before[:8], changes[0]["to"])

    def test_a_component_the_target_does_not_have_is_removed(self):
        changes = upgrade.component_changes(self.base, "HEAD~1")

        self.assertEqual([{"name": "child", "from": upgrade._tree_components(
            self.base, "HEAD")["child"][:8], "to": None, "change": "removed"}], changes)

    def test_nothing_changed_is_an_empty_list_rather_than_a_claim(self):
        self.assertEqual([], upgrade.component_changes(self.base, "HEAD"))

    def test_a_checkout_with_no_submodules_has_no_components(self):
        """Standalone mutint-core. The sentence then says nothing about components at all."""
        self.assertEqual({}, upgrade._tree_components(self.child, "HEAD"))

    def test_an_annotated_tag_is_described_by_the_commit_it_points_at(self):
        """`rev-parse v0.0.1` answers the *tag object*, which appears in no log and is not
        what an upgrade moves to; `show -s` on one prints its header rather than a date."""
        _run(self.base, "tag", "-a", "v1.0.0", "-m", "release")
        _run(self.base, "remote", "add", "origin", self.child)

        described = upgrade.describe(self.base, "v1.0.0", "tag")

        commit = upgrade._git(self.base, "rev-parse", "--short=8", "HEAD").strip()
        self.assertEqual(commit, described["sha"])
        self.assertNotIn("tag", (described.get("date") or ""))
        self.assertRegex(described["date"], r"^\d{4}-\d{2}-\d{2}T")

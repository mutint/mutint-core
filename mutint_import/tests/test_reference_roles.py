"""Which breseq reference option each contig is passed under.

The guess's table is the bulk of this: it is the one part of the feature that decides
something without being asked, so what it must *not* claim matters as much as what it must.

The carry-across tests are the other half, and they guard an invariant maintained by hand
in two places -- `reference_store._apply_sequence_fields` and
`reference_rename._record_aliases` -- both of which rebuild `seq_ids` from the sequences
alone and would silently drop a role that nobody carried.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_experiment.models import Experiment, Project
from mutint_import import annotation, reference, reference_roles, reference_store
from mutint_import.reference_roles import (
    ROLE_CONTIG,
    ROLE_JUNCTION_ONLY,
    ROLE_REFERENCE,
    guess_role,
)
from mutint_import.tests import breseq_fixture


class GuessTestCase(TestCase):
    """Pure: no database, no reference loaded."""

    ASSEMBLED = [
        "NODE_1_length_842113_cov_12.4",   # SPAdes, as it really writes them
        "NODE_1", "node_7", "NODE-2", "NODE.3",
        "contig_00007", "Contig1", "contig-12",
        "ctg-3", "CTG_12",
        "scaffold12", "scaffold_1", "scf_9",
        "k141_12345", "k99.7",             # megahit
    ]

    #: Every one of these is a real thing somebody's reference is called, and each would be
    #: a silent wrong answer if the pattern claimed it.
    NOT_ASSEMBLED = [
        "pKD46", "NC_000913", "REL606", "IS150", "SYN001",
        "chromosome", "plasmid_pB1",
        "JAABCD010000001",                 # a WGS accession: digits, but not our shape
        "Contigo", "NODEL",                # words that merely start the same way
        "contig", "scaffold", "ctg", "k141",  # the bare word, with no number after it
        "", None,
    ]

    def test_an_assembler_name_is_guessed_as_a_contig(self):
        for name in self.ASSEMBLED:
            self.assertEqual(guess_role(name), ROLE_CONTIG, name)

    def test_everything_else_is_a_plain_reference(self):
        for name in self.NOT_ASSEMBLED:
            self.assertEqual(guess_role(name), ROLE_REFERENCE, repr(name))

    def test_junction_only_is_never_guessed(self):
        """There is no name that reliably means 'this sequence is not in the genome'.

        Guessing it wrong takes every mutation on a contig out of the results silently,
        which is not a mistake worth a heuristic.
        """
        names = self.ASSEMBLED + [name for name in self.NOT_ASSEMBLED if name]
        self.assertNotIn(ROLE_JUNCTION_ONLY, {guess_role(name) for name in names})


class EntryTestCase(TestCase):
    def test_an_absent_key_reads_as_the_guess(self):
        entry = {"id": "NODE_1"}
        self.assertEqual(reference_roles.entry_role(entry), ROLE_CONTIG)
        self.assertTrue(reference_roles.entry_is_guessed(entry))

    def test_an_explicit_role_beats_the_guess(self):
        """Including the case that looks like a no-op and is the whole point of storing it:
        an assembler-named contig somebody has deliberately set back to `-r`."""
        entry = {"id": "NODE_1", "role": ROLE_REFERENCE}
        self.assertEqual(reference_roles.entry_role(entry), ROLE_REFERENCE)
        self.assertFalse(reference_roles.entry_is_guessed(entry))

    def test_an_unknown_stored_role_falls_back_to_the_guess(self):
        entry = {"id": "NODE_1", "role": "nonsense"}
        self.assertEqual(reference_roles.entry_role(entry), ROLE_CONTIG)
        self.assertTrue(reference_roles.entry_is_guessed(entry))


#: A third distinct sequence; `breseq_fixture` ships two.
SEQUENCE_C = "TTACGGCA" * 12  # 96 bases


class _Reference(TestCase):
    """An experiment with a three-contig reference established through the real path."""

    SEQUENCES = [("NODE_1", breseq_fixture.SEQUENCE_A),
                 ("NODE_2", breseq_fixture.SEQUENCE_B),
                 ("pKD46", SEQUENCE_C)]

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.drop = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        self.addCleanup(shutil.rmtree, self.drop, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)

        # `Project.user` is NOT NULL, so a project needs an owner even where no test
        # asserts anything about access.
        owner = User.objects.create(username="owner")
        project = Project.objects.create(name="P", user=owner)
        self.experiment = Experiment.objects.create(name="e", project=project)
        self._establish(self.SEQUENCES)

    def _establish(self, sequences, **kwargs):
        """Through the real path: write a FASTA and normalize it, as a drop does."""
        gff3_text, sequences = self._normalize(sequences, "ref.fasta")
        return reference_store.establish_or_check(
            self.experiment, gff3_text, sequences, **kwargs)

    def _normalize(self, sequences, filename):
        path = os.path.join(self.drop, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(reference.render_fasta(sequences))
        return reference.normalize_reference(path)

    def _refresh(self):
        self.experiment.refresh_from_db()
        if hasattr(self.experiment, "_prefetched_objects_cache"):
            self.experiment._prefetched_objects_cache = {}
        try:
            del self.experiment.reference
        except AttributeError:
            pass
        return self.experiment


class RolesForTestCase(_Reference):
    def test_every_contig_gets_a_role_with_nothing_set(self):
        roles = reference_roles.roles_for(self.experiment)
        self.assertEqual(roles, {"NODE_1": ROLE_CONTIG,
                                 "NODE_2": ROLE_CONTIG,
                                 "pKD46": ROLE_REFERENCE})

    def test_an_experiment_with_no_reference_has_no_roles(self):
        other = Experiment.objects.create(name="bare")
        self.assertEqual(reference_roles.roles_for(other), {})
        self.assertEqual(reference_roles.grouped(other), [])
        self.assertEqual(reference_roles.describe(other), "")
        self.assertTrue(reference_roles.is_uniform(other))

    def test_setting_a_role_sticks_and_stops_being_a_guess(self):
        changed = reference_roles.set_roles(self.experiment,
                                            {"pKD46": ROLE_JUNCTION_ONLY})
        self.assertEqual(changed, 1)
        states = reference_roles.states_for(self._refresh())
        self.assertEqual(states["pKD46"]["role"], ROLE_JUNCTION_ONLY)
        self.assertFalse(states["pKD46"]["guessed"])

    def test_clearing_puts_it_back_on_the_suggestion(self):
        reference_roles.set_roles(self.experiment, {"NODE_1": ROLE_REFERENCE})
        self.assertFalse(reference_roles.states_for(self._refresh())["NODE_1"]["guessed"])
        reference_roles.set_roles(self.experiment, {"NODE_1": None})
        states = reference_roles.states_for(self._refresh())
        self.assertEqual(states["NODE_1"]["role"], ROLE_CONTIG)
        self.assertTrue(states["NODE_1"]["guessed"])

    def test_setting_a_role_to_what_it_already_effectively_is_changes_nothing_visible(self):
        """It is still recorded -- that is what makes it stop being a suggestion -- but it
        is not reported as a change, because nothing about the analysis moved."""
        changed = reference_roles.set_roles(self.experiment, {"NODE_1": ROLE_CONTIG})
        self.assertEqual(changed, 0)
        self.assertFalse(reference_roles.states_for(self._refresh())["NODE_1"]["guessed"])

    def test_an_unknown_seq_id_is_ignored_rather_than_raised(self):
        reference_roles.set_roles(self.experiment, {"nope": ROLE_CONTIG})
        self.assertNotIn("nope", reference_roles.roles_for(self._refresh()))

    def test_is_uniform_only_when_everything_is_a_plain_reference(self):
        self.assertFalse(reference_roles.is_uniform(self.experiment))
        reference_roles.set_roles(self.experiment, {"NODE_1": ROLE_REFERENCE,
                                                    "NODE_2": ROLE_REFERENCE})
        self.assertTrue(reference_roles.is_uniform(self._refresh()))

    def test_grouped_is_in_role_order_and_omits_empty_roles(self):
        self.assertEqual(reference_roles.grouped(self.experiment),
                         [(ROLE_REFERENCE, ["pKD46"]),
                          (ROLE_CONTIG, ["NODE_1", "NODE_2"])])

    def test_describe_names_a_lone_sequence_and_counts_a_group(self):
        reference_roles.set_roles(self.experiment, {"pKD46": ROLE_JUNCTION_ONLY})
        self.assertEqual(reference_roles.describe(self._refresh()),
                         "2 sequences (-c), pKD46 (-s)")


class CarryAcrossTestCase(_Reference):
    """The invariant maintained by hand in two places."""

    def test_a_role_survives_an_annotation_replacement(self):
        reference_roles.set_roles(self.experiment, {"pKD46": ROLE_JUNCTION_ONLY,
                                                    "NODE_1": ROLE_REFERENCE})
        # Same sequences, different annotation -- the path `install_annotation` takes.
        gff3_text, sequences = self._normalize(self.SEQUENCES, "again.fasta")
        annotation.install_annotation(self.experiment, gff3_text, sequences)

        states = reference_roles.states_for(self._refresh())
        self.assertEqual(states["pKD46"]["role"], ROLE_JUNCTION_ONLY)
        self.assertFalse(states["pKD46"]["guessed"])
        self.assertEqual(states["NODE_1"]["role"], ROLE_REFERENCE)
        self.assertFalse(states["NODE_1"]["guessed"],
                         "an explicit role must not decay back into a suggestion")

    def test_a_role_survives_a_contig_rename(self):
        """A rename changes what a contig is called, not what it is."""
        from mutint_import import reference_rename

        reference_roles.set_roles(self.experiment, {"pKD46": ROLE_JUNCTION_ONLY})
        renamed = [("chr_1", breseq_fixture.SEQUENCE_A),
                   ("chr_2", breseq_fixture.SEQUENCE_B),
                   ("pKD46_v2", SEQUENCE_C)]
        reference = self.experiment.reference
        plan = reference_rename.plan_rename(reference, renamed)
        reference_rename.apply_rename(self.experiment, reference, plan)

        states = reference_roles.states_for(self._refresh())
        self.assertEqual(states["pKD46_v2"]["role"], ROLE_JUNCTION_ONLY)
        self.assertFalse(states["pKD46_v2"]["guessed"])

    def test_a_guessed_role_re_guesses_against_the_new_name(self):
        """Nothing was recorded for these, so the suggestion follows the name -- which is
        the answer somebody would get if the contigs had been called this to begin with."""
        from mutint_import import reference_rename

        renamed = [("chr_1", breseq_fixture.SEQUENCE_A),
                   ("chr_2", breseq_fixture.SEQUENCE_B),
                   ("pKD46", SEQUENCE_C)]
        reference = self.experiment.reference
        plan = reference_rename.plan_rename(reference, renamed)
        reference_rename.apply_rename(self.experiment, reference, plan)

        states = reference_roles.states_for(self._refresh())
        self.assertEqual(states["chr_1"]["role"], ROLE_REFERENCE)
        self.assertTrue(states["chr_1"]["guessed"])


class RenderedGroupsTestCase(_Reference):
    def test_each_contig_lands_in_exactly_one_file_and_none_is_lost(self):
        reference_roles.set_roles(self.experiment, {"pKD46": ROLE_JUNCTION_ONLY})
        references = annotation.reference_sequences_for(self.experiment)
        groups = reference_roles.rendered_groups(self.experiment, references)

        self.assertEqual([(role, flag) for role, flag, _text in groups],
                         [(ROLE_CONTIG, "-c"), (ROLE_JUNCTION_ONLY, "-s")])

        seen = []
        for _role, _flag, text in groups:
            names = [line.split("\t")[1] for line in text.splitlines()
                     if line.startswith("##sequence-region")]
            seen.extend(names)
        self.assertEqual(sorted(seen), ["NODE_1", "NODE_2", "pKD46"])
        self.assertEqual(len(seen), len(set(seen)), "a contig must not be in two files")

    def test_a_uniform_reference_renders_the_whole_stored_file(self):
        """So the `-r`-only fast path and the rendered path cannot disagree about bytes."""
        reference_roles.set_roles(self.experiment, {"NODE_1": ROLE_REFERENCE,
                                                    "NODE_2": ROLE_REFERENCE})
        experiment = self._refresh()
        references = annotation.reference_sequences_for(experiment)
        groups = reference_roles.rendered_groups(experiment, references)
        self.assertEqual(len(groups), 1)
        _role, flag, text = groups[0]
        self.assertEqual(flag, "-r")

        from mutint_common import store
        with open(store.experiment_reference_path(experiment.id, store.REFERENCE_GFF3),
                  "r", encoding="utf-8") as handle:
            self.assertEqual(text, handle.read())

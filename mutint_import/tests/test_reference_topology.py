"""Whether each contig is circular or linear: read from the file, kept in two places.

The invariant under test is that `seq_ids[].circular` and the stored GFF3's `region` rows
agree -- the file is what breseq is handed and what every export is rendered from -- and
that both survive the two paths that rebuild `seq_ids` from the sequences alone
(`reference_store._apply_sequence_fields`, `reference_rename._record_aliases`).
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from mutint_common import store
from mutint_experiment.models import Experiment, Project
from mutint_import import (annotation, reference, reference_export, reference_rename,
                           reference_roles, reference_store, reference_topology)
from mutint_import.annotate import genbank, gff3
from mutint_import.annotate.model import AnnotatedSequence, LoadedReferenceSequences
from mutint_import.reference_roles import ROLE_JUNCTION_ONLY
from mutint_import.tests import breseq_fixture

SEQUENCE_C = "TTACGGCA" * 12  # 96 bases


def _gff3(sequences, circular=(), annotated=False):
    """Canonical text: pragmas, region rows for `circular` (`{seq_id: bool}`), FASTA.

    `annotated` adds one CDS on the first contig, which is what makes the text count as
    annotation to `reference_rename.has_annotation` -- a region row alone does not.
    """
    circular = dict(circular)
    lines = ["##gff-version 3"]
    for seq_id, bases in sequences:
        lines.append("##sequence-region\t%s\t1\t%d" % (seq_id, len(bases)))
    lines.extend(gff3.region_rows(
        (seq_id, len(bases), circular.get(seq_id)) for seq_id, bases in sequences))
    if annotated:
        lines.append("\t".join([sequences[0][0], "mutint", "CDS", "4", "30", ".", "+", "0",
                                "Alias=b0001;ID=b0001;Name=thrA;Note=x;transl_table=11"]))
    lines.append("##FASTA")
    lines.append(reference.render_fasta(sequences).rstrip("\n"))
    return "\n".join(lines) + "\n"


def _genbank(seq_id, bases, topology=None):
    """A minimal GenBank record, with or without a LOCUS topology."""
    locus = "LOCUS       %-16s %d bp    DNA     %-8s BCT 01-JAN-2000" % (
        seq_id, len(bases), topology or "")
    lines = [locus,
             "DEFINITION  test.",
             "ACCESSION   %s" % seq_id,
             "VERSION     %s.1" % seq_id,
             "FEATURES             Location/Qualifiers",
             "     source          1..%d" % len(bases),
             "ORIGIN"]
    for offset in range(0, len(bases), 60):
        chunk = bases[offset:offset + 60].lower()
        lines.append("%9d %s" % (offset + 1, " ".join(
            chunk[i:i + 10] for i in range(0, len(chunk), 10))))
    lines.append("//")
    return "\n".join(lines) + "\n"


class EntryTestCase(SimpleTestCase):
    def test_an_absent_key_is_linear_and_suggested(self):
        entry = {"id": "chr"}
        self.assertFalse(reference_topology.entry_circular(entry))
        self.assertTrue(reference_topology.entry_is_guessed(entry))
        self.assertEqual(reference_topology.label_for(False), "linear")

    def test_a_stored_answer_is_not_a_suggestion(self):
        for value in (True, False):
            entry = {"id": "chr", "circular": value}
            self.assertEqual(reference_topology.entry_circular(entry), value)
            self.assertFalse(reference_topology.entry_is_guessed(entry))

    def test_parse_takes_the_page_s_words_and_refuses_others(self):
        self.assertIs(reference_topology.parse("circular"), True)
        self.assertIs(reference_topology.parse("linear"), False)
        self.assertIs(reference_topology.parse(True), True)
        self.assertIsNone(reference_topology.parse(None))
        with self.assertRaises(ValueError):
            reference_topology.parse("round")


class ParsersTestCase(SimpleTestCase):
    """Each loader fills `AnnotatedSequence.circular`, and only when the file said."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def _write(self, name, text):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_a_genbank_s_locus_topology_is_read(self):
        path = self._write("c.gbk", _genbank("chr", breseq_fixture.SEQUENCE_A, "circular"))
        self.assertIs(genbank.load_genbank(path)["chr"].circular, True)
        path = self._write("l.gbk", _genbank("chr", breseq_fixture.SEQUENCE_A, "linear"))
        self.assertIs(genbank.load_genbank(path)["chr"].circular, False)

    def test_a_genbank_that_says_nothing_leaves_it_unknown(self):
        path = self._write("n.gbk", _genbank("chr", breseq_fixture.SEQUENCE_A))
        self.assertIsNone(genbank.load_genbank(path)["chr"].circular)

    def test_a_gff3_region_row_is_read_and_its_absence_is_unknown(self):
        sequences = [("chr", breseq_fixture.SEQUENCE_A), ("p", breseq_fixture.SEQUENCE_B)]
        path = self._write("r.gff3", _gff3(sequences, {"chr": True}))
        loaded = gff3.load_gff3(path)
        self.assertIs(loaded["chr"].circular, True)
        self.assertIsNone(loaded["p"].circular)

    def test_breseq_s_own_fixture_says_linear(self):
        fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "annotate", "tests", "fixtures")
        loaded = gff3.load_gff3(os.path.join(fixtures, "synthetic.gff3"))
        self.assertIs(loaded["SYN001"].circular, False)

    def test_a_fasta_records_nothing(self):
        path = self._write("s.fasta", reference.render_fasta(
            [("chr", breseq_fixture.SEQUENCE_A)]))
        gff3_text, sequences = reference.normalize_reference(path)
        self.assertEqual(reference.gff3_topologies(gff3_text), {})
        self.assertNotIn("circular", reference.sequence_entries(sequences)[0])


class RendererTestCase(SimpleTestCase):
    def _references(self, **circular):
        references = LoadedReferenceSequences()
        for seq_id, bases in (("chr", breseq_fixture.SEQUENCE_A),
                              ("p", breseq_fixture.SEQUENCE_B)):
            references.add(AnnotatedSequence(seq_id, bases, circular=circular.get(seq_id)))
        return references

    def test_a_region_row_is_written_only_for_a_known_topology(self):
        text = gff3.render_breseq_gff3(self._references(chr=True))
        self.assertIn("chr\tmutint\tregion\t1\t%d\t.\t+\t.\tIs_circular=true"
                      % len(breseq_fixture.SEQUENCE_A), text)
        self.assertNotIn("p\tmutint\tregion", text)
        self.assertEqual(reference.gff3_topologies(text), {"chr": True})

    def test_the_block_sits_between_the_pragmas_and_the_features(self):
        text = gff3.render_breseq_gff3(self._references(chr=True, p=False))
        lines = text.splitlines()
        self.assertTrue(lines[1].startswith("##sequence-region"))
        self.assertTrue(lines[2].startswith("##sequence-region"))
        self.assertIn("\tregion\t", lines[3])
        self.assertIn("\tregion\t", lines[4])
        self.assertEqual(lines[5], "##FASTA")

    def test_it_round_trips_through_the_reader(self):
        references = self._references(chr=True, p=False)
        text = gff3.render_breseq_gff3(references)
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "r.gff3")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        loaded = gff3.load_gff3(path)
        self.assertIs(loaded["chr"].circular, True)
        self.assertIs(loaded["p"].circular, False)
        self.assertEqual(gff3.render_breseq_gff3(loaded), text)

    def test_with_topology_is_a_fixed_point_of_the_renderer(self):
        references = self._references(chr=True, p=False)
        text = gff3.render_breseq_gff3(references)
        self.assertEqual(
            reference.with_topology(text, reference.topologies_of(references)), text)

    def test_with_topology_replaces_drops_and_adds_rows(self):
        sequences = [("chr", breseq_fixture.SEQUENCE_A), ("p", breseq_fixture.SEQUENCE_B)]
        text = _gff3(sequences, {"chr": True})
        flipped = reference.with_topology(text, {"chr": False, "p": True})
        self.assertEqual(reference.gff3_topologies(flipped), {"chr": False, "p": True})
        self.assertEqual(flipped, _gff3(sequences, {"chr": False, "p": True}))
        cleared = reference.with_topology(flipped, {})
        self.assertEqual(cleared, _gff3(sequences))

    def test_a_region_row_is_not_annotation(self):
        text = _gff3([("chr", breseq_fixture.SEQUENCE_A)], {"chr": True})
        self.assertFalse(reference_rename.has_annotation(text))

    def test_the_genbank_export_carries_a_known_topology_only(self):
        records = reference_export.to_genbank_records(self._references(chr=True))
        by_id = {record.id: record for record in records}
        self.assertEqual(by_id["chr"].annotations["topology"], "circular")
        self.assertNotIn("topology", by_id["p"].annotations)


class _Reference(TestCase):
    """An experiment with a three-contig reference established through the real path."""

    SEQUENCES = [("chr", breseq_fixture.SEQUENCE_A),
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

        owner = User.objects.create(username="owner")
        project = Project.objects.create(name="P", user=owner)
        self.experiment = Experiment.objects.create(name="e", project=project)

    def _establish(self, gff3_text, sequences, **kwargs):
        return reference_store.establish_or_check(
            self.experiment, gff3_text, sequences, **kwargs)

    def _refresh(self):
        self.experiment.refresh_from_db()
        try:
            del self.experiment.reference
        except AttributeError:
            pass
        return self.experiment

    def _stored_text(self):
        path = store.experiment_reference_path(self.experiment.id, store.REFERENCE_GFF3)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def _entries(self):
        return {entry["id"]: entry for entry in self._refresh().reference.seq_ids}


class StoreTestCase(_Reference):
    def test_what_the_file_said_lands_on_the_entries(self):
        self._establish(_gff3(self.SEQUENCES, {"chr": True, "pKD46": True}), self.SEQUENCES)
        entries = self._entries()
        self.assertIs(entries["chr"]["circular"], True)
        self.assertIs(entries["pKD46"]["circular"], True)
        self.assertNotIn("circular", entries["NODE_2"])
        states = reference_topology.states_for(self._refresh())
        self.assertEqual(states["chr"], {"circular": True, "label": "circular",
                                         "guessed": False})
        self.assertEqual(states["NODE_2"], {"circular": False, "label": "linear",
                                            "guessed": True})

    def test_setting_writes_the_row_and_the_file(self):
        self._establish(_gff3(self.SEQUENCES), self.SEQUENCES)
        before = self._refresh().reference.gff3_sha256

        changed = reference_topology.set_topology(self._refresh(), {"chr": True})

        self.assertEqual(changed, 1)
        self.assertIs(self._entries()["chr"]["circular"], True)
        self.assertEqual(reference.gff3_topologies(self._stored_text()), {"chr": True})
        reference_row = self._refresh().reference
        self.assertNotEqual(reference_row.gff3_sha256, before)
        self.assertEqual(reference_row.gff3_sha256,
                         reference_store.digest(self._stored_text()))

    def test_clearing_takes_the_row_out_of_the_file_too(self):
        self._establish(_gff3(self.SEQUENCES, {"chr": True}), self.SEQUENCES)
        reference_topology.set_topology(self._refresh(), {"chr": None})
        self.assertNotIn("circular", self._entries()["chr"])
        self.assertEqual(reference.gff3_topologies(self._stored_text()), {})

    def test_setting_linear_explicitly_is_an_answer_not_a_suggestion(self):
        self._establish(_gff3(self.SEQUENCES), self.SEQUENCES)
        changed = reference_topology.set_topology(self._refresh(), {"chr": False})
        self.assertEqual(changed, 0, "the effective topology did not move")
        self.assertFalse(reference_topology.states_for(self._refresh())["chr"]["guessed"])
        self.assertEqual(reference.gff3_topologies(self._stored_text()), {"chr": False})

    def test_the_file_still_loads_and_breseq_s_role_files_carry_the_row(self):
        self._establish(_gff3(self.SEQUENCES), self.SEQUENCES)
        reference_topology.set_topology(self._refresh(), {"chr": True})
        reference_roles.set_roles(self._refresh(), {"pKD46": ROLE_JUNCTION_ONLY})
        references = annotation.reference_sequences_for(self._refresh())
        self.assertIs(references["chr"].circular, True)
        groups = dict((role, text) for role, _flag, text
                      in reference_roles.rendered_groups(self._refresh(), references))
        self.assertEqual(reference.gff3_topologies(groups["reference"]), {"chr": True})
        self.assertEqual(reference.gff3_topologies(groups["junction_only"]), {})


class CarryAcrossTestCase(_Reference):
    def test_a_stored_answer_beats_a_replacement_annotation_s_and_the_file_fills_gaps(self):
        self._establish(_gff3(self.SEQUENCES), self.SEQUENCES)
        reference_topology.set_topology(self._refresh(), {"chr": True})

        # A new annotation says chr is linear and pKD46 is circular: the stored answer
        # wins for chr, the file fills in pKD46, and the written file says exactly that.
        annotation.install_annotation(
            self._refresh(),
            _gff3(self.SEQUENCES, {"chr": False, "pKD46": True}, annotated=True),
            self.SEQUENCES)

        entries = self._entries()
        self.assertIs(entries["chr"]["circular"], True)
        self.assertIs(entries["pKD46"]["circular"], True)
        self.assertEqual(reference.gff3_topologies(self._stored_text()),
                         {"chr": True, "pKD46": True})
        self.assertEqual(self._refresh().reference.gff3_sha256,
                         reference_store.digest(self._stored_text()))

    def test_it_survives_a_contig_rename_under_the_old_name(self):
        self._establish(_gff3(self.SEQUENCES, {"pKD46": True}), self.SEQUENCES)
        renamed = [("chr_1", breseq_fixture.SEQUENCE_A),
                   ("chr_2", breseq_fixture.SEQUENCE_B),
                   ("pKD46_v2", SEQUENCE_C)]
        gff3_text = _gff3(renamed)  # a bare rename says nothing about topology
        self._establish(gff3_text, renamed, allow_rename=True)

        entries = self._entries()
        self.assertIs(entries["pKD46_v2"]["circular"], True)
        self.assertNotIn("circular", entries["chr_1"])
        self.assertEqual(reference.gff3_topologies(self._stored_text()), {"pKD46_v2": True})

    def test_a_breseq_folder_s_reference_neither_fills_nor_overrides(self):
        """The same-sequence, no-update branch: identity is refreshed and nothing else."""
        self._establish(_gff3(self.SEQUENCES), self.SEQUENCES)
        self._establish(_gff3(self.SEQUENCES, {"chr": True}), self.SEQUENCES,
                        update_annotation=False)
        self.assertNotIn("circular", self._entries()["chr"])
        self.assertEqual(reference.gff3_topologies(self._stored_text()), {})

"""What a sample was made from: the record, and what each import path writes into it.

The record is `Sample.supplemental_data["mutint_core"]["inputs"]` rather than a table, which is
the supplemental column's own test -- it arrives with the import, dies with the sample, and is
read whole. Nothing filters on it.
"""

import os
import shutil
import tempfile

from django.test import TestCase, override_settings

from mutint_experiment.models import Project
from mutint_import import breseq_folder, gd_import
from mutint_import.tests import breseq_fixture
from mutint_sample import inputs
from mutint_sample.models import Sample


class RecordTestCase(TestCase):
    def setUp(self):
        self.sample = Sample()

    def test_a_sample_starts_with_nothing_to_say(self):
        """No backfill, so every sample imported before this has an empty record -- and an
        empty list is what the page renders no box for."""
        self.assertEqual([], self.sample.inputs)

    def test_entries_round_trip(self):
        inputs.record_inputs(self.sample, [
            inputs.Input(inputs.KIND_READS, "s_R1.fastq.gz", 1, 1),
            inputs.Input(inputs.KIND_READS, "s_R2.fastq.gz", 1, 2),
        ], save=False)

        self.assertEqual(
            [{"kind": "reads", "value": "s_R1.fastq.gz", "group": 1, "mate": 1},
             {"kind": "reads", "value": "s_R2.fastq.gz", "group": 1, "mate": 2}],
            self.sample.inputs)

    def test_recording_again_replaces_rather_than_appends(self):
        """A re-import supersedes a sample rather than adding to it -- its calls are cleared
        and rewritten, and its inputs should go the same way instead of accumulating an entry
        per re-run."""
        inputs.record_inputs(self.sample, [inputs.Input(inputs.KIND_READS, "old.fastq")],
                             save=False)
        inputs.record_inputs(self.sample, [inputs.Input(inputs.KIND_READS, "new.fastq")],
                             save=False)

        self.assertEqual(["new.fastq"], [entry["value"] for entry in self.sample.inputs])

    def test_it_leaves_the_other_groups_alone(self):
        """`set_record` merges at the component level, so the shared column's lost-update
        hazard does not apply -- asserted rather than assumed, because it is the whole reason
        four writers can share one column."""
        self.sample.set_record(Sample.COMPONENT, Sample.BRESEQ, {"reads": 1000}, save=False)

        inputs.record_inputs(self.sample, [inputs.Input(inputs.KIND_SRA, "SRR37077254")],
                             save=False)

        self.assertEqual({"reads": 1000}, self.sample.breseq)
        self.assertEqual("SRR37077254", self.sample.inputs[0]["value"])

    def test_an_accession_links_out_and_a_filename_does_not(self):
        inputs.record_inputs(self.sample, [
            inputs.Input(inputs.KIND_SRA, "SRR37077254"),
            inputs.Input(inputs.KIND_READS, "s_R1.fastq"),
        ], save=False)
        accession, read = self.sample.inputs

        self.assertEqual("https://www.ncbi.nlm.nih.gov/sra/SRR37077254",
                         inputs.url_for(accession))
        self.assertIsNone(inputs.url_for(read))
        self.assertEqual("SRA run", inputs.label_for(accession["kind"]))

    def test_an_unknown_kind_still_has_something_to_show(self):
        self.assertEqual("whatever", inputs.label_for("whatever"))


class ReadseqTestCase(TestCase):
    """breseq names its own read files in `#=READSEQ`, one line per file."""

    def test_every_readseq_line_is_read(self):
        document = gd_import._parse_document(_gd_bytes(
            "#=READSEQ\ts_R1.fastq", "#=READSEQ\ts_R2.fastq"))

        self.assertEqual(["s_R1.fastq", "s_R2.fastq"],
                         gd_import.read_files_named_by(document))

    def test_a_gd_with_no_readseq_names_nothing(self):
        self.assertEqual([], gd_import.read_files_named_by(_parse()))

    def test_a_document_whose_metadata_is_a_plain_dict_is_handled(self):
        """`vcf_import._AsGenomeDiff` hands over a plain dict, so `getlist` cannot be assumed."""

        class Plain:
            metadata = {"READSEQ": "only.fastq"}

        self.assertEqual(["only.fastq"], gd_import.read_files_named_by(Plain()))
        self.assertEqual([], gd_import.read_files_named_by(object()))


def _gd_bytes(*headers):
    lines = ["#=GENOME_DIFF\t1.0"] + list(headers) + ["SNP\t1\t.\ttest_ref\t5\tA"]
    return _Uploaded("\n".join(lines).encode("utf-8"))


def _parse(*headers):
    return gd_import._parse_document(_gd_bytes(*headers))


class _Uploaded:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw


@override_settings(MUTINT_STORE_DIR=None)
class FolderImportTestCase(TestCase):
    """What a breseq folder dropped on the Import data page records."""

    def setUp(self):
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

        from django.contrib.auth.models import User
        self.owner = User.objects.create(username="o", email="o@e.com")
        Project.objects.create(name="p", user=self.owner)

    def _import(self, sample_name, headers=()):
        gd_text = breseq_fixture.GD_TEXT
        if headers:
            first, rest = gd_text.split("\n", 1)
            gd_text = "\n".join([first] + list(headers) + [rest])
        breseq_fixture.write_sample(self.root, sample_name, gd_text=gd_text)
        return breseq_folder.import_breseq_folders(self.root, "p", "e", "o")

    def test_a_folder_records_the_read_files_its_gd_names(self):
        """The point of reading `READSEQ`: a manually uploaded folder can say what the reads
        were called, not merely what the folder was."""
        self._import("s1", headers=("#=READSEQ\ta_R1.fastq.gz",
                                    "#=READSEQ\ta_R2.fastq.gz"))

        sample = Sample.objects.get()
        self.assertEqual(["a_R1.fastq.gz", "a_R2.fastq.gz"],
                         [entry["value"] for entry in sample.inputs])
        self.assertEqual({"reads"}, {entry["kind"] for entry in sample.inputs})

    def test_a_folder_whose_gd_names_no_reads_records_the_folder(self):
        """A `data/` tree assembled by hand has only its own name to offer."""
        self._import("s1")

        sample = Sample.objects.get()
        self.assertEqual([{"kind": "folder", "value": "s1", "group": 0, "mate": None}],
                         sample.inputs)

    def test_reads_are_recorded_ungrouped(self):
        """breseq's rule for which files are mates lives in mutint-breseq, so core says what
        the files were and not how they paired. A run launched through the plugin records the
        pairing it actually used."""
        self._import("s1", headers=("#=READSEQ\ta_R1.fastq", "#=READSEQ\ta_R2.fastq"))

        sample = Sample.objects.get()
        self.assertEqual([None, None], [entry["mate"] for entry in sample.inputs])
        self.assertEqual(2, len({entry["group"] for entry in sample.inputs}))

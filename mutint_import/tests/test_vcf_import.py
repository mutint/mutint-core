"""Importing VCF, and giving it back.

The claim that matters is the first test: a VCF and a `.gd` of the same call land on **one**
`Mutation`. Everything else in this design follows from that being true, and it is true only
because both paths go through `gd_import._database_gd_mutations`.
"""

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_experiment.models import Project
from mutint_import import reference, reference_store, vcf, vcf_export, vcf_import
from mutint_import.gd_import import _parse_document, import_document_as_sample, prepare_experiment_by_id
from mutint_import.tests import breseq_fixture
from mutint_sample.models import Mutation, MutationCall, Sample

# The fixture reference is 160 bases of ACGT repeated, as `test_ref`.
SEQUENCE = breseq_fixture.SEQUENCE_A


def vcf_text(rows, samples=("s1",), header=None):
    lines = list(header or ["##fileformat=VCFv4.2",
                            "##contig=<ID=test_ref,length=%d>" % len(SEQUENCE)])
    columns = list(vcf.FIXED_COLUMNS)
    if samples:
        columns += ["FORMAT"] + list(samples)
    lines.append("#" + "\t".join(columns))
    lines.extend(rows)
    return "\n".join(lines) + "\n"


class VcfImportTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.user)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)
        self._establish_reference()
        self.context = prepare_experiment_by_id(self.experiment.id)

    def _establish_reference(self):
        sequences = [("test_ref", SEQUENCE)]
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "ref.gff3")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(breseq_fixture.gff3_text(sequences))
        normalized, parsed = reference.normalize_reference(path)
        reference_store.establish_or_check(self.experiment, normalized, parsed)

    def _import(self, text, filename="s1.vcf"):
        import io
        document = vcf.read(io.StringIO(text), filename)
        names = vcf_import.sample_names_for(document, filename)
        results = []
        for name in names:
            results.append(
                vcf_import.import_sample(document, name, self.context, self.experiment))
        return document, results

    # --- the anchor claim ----------------------------------------------------------------

    def test_a_vcf_and_a_gd_of_the_same_call_are_one_mutation(self):
        """The whole design in one assertion.

        `SNP test_ref 100 A` as GenomeDiff, and the same call spelled as VCF. If these fork
        into two rows then every cross-sample table shows the same mutation twice and nothing
        else in this feature matters.
        """
        import io

        gd_text = ("#=GENOME_DIFF\t1.0\n"
                   "SNP\t1\t.\ttest_ref\t100\tA\tfrequency=1\n")
        import_document_as_sample(
            _parse_document(io.BytesIO(gd_text.encode())), "from_gd", self.context)

        # Position 100 of ACGT*40 is 'T'; the .gd says it became 'A'.
        self.assertEqual("T", SEQUENCE[99])
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"]),
                     filename="from_vcf.vcf")

        self.assertEqual(1, Mutation.objects.filter(experiment=self.experiment).count())
        mutation = Mutation.objects.get(experiment=self.experiment)
        self.assertEqual(2, MutationCall.objects.filter(mutation=mutation).count())
        self.assertEqual({"breseq", "vcf"},
                         set(MutationCall.objects.values_list("source", flat=True)))

    def test_an_indel_agrees_across_the_two_formats_too(self):
        """A deletion is where the anchor base makes the two spellings differ most."""
        import io

        gd_text = ("#=GENOME_DIFF\t1.0\n"
                   "DEL\t1\t.\ttest_ref\t101\t3\tfrequency=1\n")
        import_document_as_sample(
            _parse_document(io.BytesIO(gd_text.encode())), "from_gd", self.context)

        # VCF spells it with the anchor base at 100.
        anchor = SEQUENCE[99]
        deleted = SEQUENCE[100:103]
        self._import(
            vcf_text(["test_ref\t100\t.\t%s\t%s\t50\tPASS\t.\tGT\t1"
                      % (anchor + deleted, anchor)]),
            filename="from_vcf.vcf")

        self.assertEqual(1, Mutation.objects.filter(experiment=self.experiment).count())
        mutation = Mutation.objects.get(experiment=self.experiment)
        self.assertEqual("DEL", mutation.mutation_type)
        self.assertEqual(101, mutation.start_position)
        self.assertEqual(3, mutation.feature_length)

    # --- the ordinary path ---------------------------------------------------------------

    def test_a_sample_is_created_and_annotated(self):
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"]))

        sample = Sample.objects.get(population__experiment=self.experiment)
        self.assertEqual("s1", sample.source_name)
        mutation = Mutation.objects.get(experiment=self.experiment)
        # Annotated by the same annotator the .gd path uses -- the fixture's gene covers 50-150.
        self.assertTrue(mutation.gene)
        self.assertNotEqual("None", mutation.gene)

    def test_the_source_says_vcf(self):
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"]))
        self.assertEqual("vcf", MutationCall.objects.get().source)

    def test_frequency_comes_from_the_call(self):
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT:AF\t1:0.25"]))
        self.assertAlmostEqual(0.25, MutationCall.objects.get().frequency)

    def test_a_reference_call_makes_no_mutation(self):
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t0"]))
        self.assertEqual(0, MutationCall.objects.count())

    # --- multi-sample ---------------------------------------------------------------------

    def test_each_column_becomes_a_sample_sharing_one_mutation(self):
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1\t1\t0"],
                              samples=("a", "b", "c")),
                     filename="cohort.vcf")

        self.assertEqual({"a", "b", "c"},
                         set(Sample.objects.values_list("source_name", flat=True)))
        self.assertEqual(1, Mutation.objects.filter(experiment=self.experiment).count())
        # Only the two that called it.
        self.assertEqual(2, MutationCall.objects.count())

    def test_two_samples_choosing_different_alleles_are_two_mutations(self):
        # gdtools aborts the file here. Under haploid these are simply two mutations.
        self._import(vcf_text(["test_ref\t100\t.\tT\tA,G\t50\tPASS\t.\tGT\t1\t2"],
                              samples=("a", "b")),
                     filename="cohort.vcf")

        self.assertEqual(2, Mutation.objects.filter(experiment=self.experiment).count())
        self.assertEqual({"A", "G"},
                         set(Mutation.objects.values_list("sequence_change", flat=True)))

    def test_a_sites_only_file_is_one_sample_named_from_the_file(self):
        text = vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t."], samples=())
        self._import(text, filename="Ara-2_500gen_763A.vcf")

        sample = Sample.objects.get(population__experiment=self.experiment)
        self.assertEqual("Ara-2_500gen_763A", sample.source_name)
        # And the filename placed it on its ALE, through the one rule that does that.
        self.assertEqual("Ara-2", sample.population.name)
        self.assertEqual(500, sample.time_point)

    # --- refusals, reported rather than fatal ---------------------------------------------

    def test_a_ref_that_disagrees_with_the_reference_is_refused(self):
        """gdtools imports this silently and wrongly; it is the failure nobody catches."""
        # Position 100 is 'T'; claim it is 'A'.
        _, results = self._import(
            vcf_text(["test_ref\t100\t.\tA\tG\t50\tPASS\t.\tGT\t1"]))
        count, _replaced, problems = results[0]

        self.assertEqual(0, count)
        self.assertEqual(1, len(problems))
        self.assertIn("different genome", problems[0])

    def test_a_symbolic_allele_is_reported_and_the_rest_imports(self):
        _, results = self._import(vcf_text([
            "test_ref\t100\t.\tT\t<DEL>\t50\tPASS\tEND=200\tGT\t1",
            "test_ref\t104\t.\tT\tA\t50\tPASS\t.\tGT\t1"]))
        count, _replaced, problems = results[0]

        self.assertEqual(1, count)
        self.assertEqual(1, len(problems))
        self.assertIn("symbolic", problems[0])

    # --- round trip -----------------------------------------------------------------------

    def test_a_single_sample_vcf_exports_byte_for_byte_plus_where_it_sits(self):
        """Verbatim, with one line added: the `##SAMPLE` line saying the sample's coordinate,
        so a re-import anywhere lands where the sample is now."""
        # Bases read off the fixture rather than typed: a REF that disagrees with the
        # reference is refused, correctly, and a hand-typed one is how that gets discovered.
        rows = [
            "test_ref\t100\t.\t%s\tA\t50.5\tPASS\tDP=30;AF=0.5\tGT:AD\t1:2,8"
            % SEQUENCE[99],
            "test_ref\t120\t.\t%s\t%sAAA\t99\tq10\tDP=12\tGT:AD\t1:1,11"
            % (SEQUENCE[119], SEQUENCE[119])]
        self._import(vcf_text(rows))

        sample = Sample.objects.get(population__experiment=self.experiment)
        self.assertIsNone(sample.time_point)  # `s1` places nothing
        expected = vcf_text(rows, header=[
            "##fileformat=VCFv4.2",
            "##contig=<ID=test_ref,length=%d>" % len(SEQUENCE),
            "##SAMPLE=<ID=s1,sample=1,sample_type=clone>"])  # auto-numbered: `1`
        self.assertEqual(expected, vcf_export.export_vcf_text(sample))

        # And an unplaced sample's export still lands on itself.
        self._import(expected, filename="again.vcf")
        self.assertEqual(sample.pk,
                         Sample.objects.get(population__experiment=self.experiment).pk)

    def test_the_placement_line_carries_the_coordinate_and_is_upserted(self):
        rows = ["test_ref\t100\t.\t%s\tA\t50\tPASS\t.\tGT\t1" % SEQUENCE[99]]
        self._import(vcf_text(rows, header=[
            "##fileformat=VCFv4.2",
            "##SAMPLE=<ID=s1,Description=\"a clone, frozen\",generation=500,population=Ara-3>"]))
        sample = Sample.objects.get(population__experiment=self.experiment)
        self.assertEqual(("Ara-3", 500), (sample.population.name, sample.time_point))

        # Moved since, and mixed after all: the line says so, and keeps the description.
        sample.time_point = 1000
        sample.is_clonal = False
        sample.save()
        text = vcf_export.export_vcf_text(sample)
        lines = text.splitlines()
        self.assertEqual(
            '##SAMPLE=<ID=s1,sample=s1,population=Ara-3,time_point=1000,'
            'sample_type=population,Description="a clone, frozen">', lines[1])
        self.assertEqual(1, len([line for line in lines if line.startswith("##SAMPLE=")]))

    def test_exporting_an_imported_export_is_byte_identical(self):
        rows = ["test_ref\t100\t.\t%s\tA\t50\tPASS\t.\tGT\t1" % SEQUENCE[99]]
        self._import(vcf_text(rows, samples=("Ara-1_500gen_c1",)))
        sample = Sample.objects.get(population__experiment=self.experiment)
        first = vcf_export.export_vcf_text(sample)

        self._import(first, filename="again.vcf")
        self.assertEqual(1, Sample.objects.filter(population__experiment=self.experiment).count())
        self.assertEqual(first, vcf_export.export_vcf_text(
            Sample.objects.get(population__experiment=self.experiment)))

    def test_an_export_places_the_sample_where_it_is_now(self):
        rows = ["test_ref\t100\t.\t%s\tA\t50\tPASS\t.\tGT\t1" % SEQUENCE[99]]
        self._import(vcf_text(rows))
        sample = Sample.objects.get(population__experiment=self.experiment)
        from mutint_experiment.models import Population
        population = Population.objects.create(experiment=self.experiment, name="Ara+2")
        sample.population = population
        sample.time_point = 250
        sample.name = "c7"
        sample.save()

        text = vcf_export.export_vcf_text(sample)
        from mutint_experiment.views import _create_experiment
        other = _create_experiment(self.project, "other", self.user)
        self.experiment, kept = other, self.experiment
        self._establish_reference()
        self.experiment = kept
        context = prepare_experiment_by_id(other.id)
        import io
        document = vcf.read(io.StringIO(text), "s1.vcf")
        for name in vcf_import.sample_names_for(document, "s1.vcf"):
            vcf_import.import_sample(document, name, context, other)

        landed = Sample.objects.get(population__experiment=other)
        self.assertEqual(("Ara+2", 250, "c7"),
                         (landed.population.name, landed.time_point, landed.name))

    def test_a_sample_that_never_came_from_a_vcf_gets_a_generated_one(self):
        """A lossy view for tools that speak VCF, and the file says what it lost."""
        import io
        gd_text = ("#=GENOME_DIFF\t1.0\n#=REFSEQ\tREL606.gbk\n"
                   "SNP\t1\t.\ttest_ref\t100\tA\tfrequency=0.25\n"
                   "DEL\t2\t.\ttest_ref\t120\t3\n"
                   "MOB\t3\t.\ttest_ref\t140\tIS150\t1\t3\n"
                   "AMP\t4\t.\ttest_ref\t150\t4\t2\n")
        sample, _count, _replaced = import_document_as_sample(
            _parse_document(io.BytesIO(gd_text.encode())), "Ara-1_500gen_c3", self.context)

        text = vcf_export.export_vcf_text(sample)
        lines = text.splitlines()
        self.assertEqual("##fileformat=VCFv4.2", lines[0])
        self.assertTrue(lines[1].startswith("##source=MutInt "))
        self.assertIn("##reference=REL606.gbk", lines)
        self.assertIn("##contig=<ID=test_ref,length=%d>" % len(SEQUENCE), lines)
        self.assertIn("##SAMPLE=<ID=Ara-1_500gen_c3,sample=c3,population=Ara-1,"
                      "time_point=500,sample_type=clone>", lines)
        self.assertIn("##mutint_omitted=2  # mutations with no VCF spelling, left out of "
                      "this file: 1 AMP, 1 MOB; the sample's .gd carries them", lines)
        self.assertEqual("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
                         "Ara-1_500gen_c3", lines[-3])
        self.assertEqual("test_ref\t100\t.\t%s\tA\t.\t.\tAF=0.2500\tGT\t1" % SEQUENCE[99],
                         lines[-2])
        self.assertTrue(lines[-1].startswith("test_ref\t119\t.\t%s" % SEQUENCE[118]))

        # And it is a VCF this importer reads back onto the same sample -- which, because a
        # re-import supersedes a sample's calls, then holds only what the file could spell.
        # The file said so; that is the cost of a lossy view and the reason the .gd exists.
        self._import(text, filename="generated.vcf")
        self.assertEqual(1, Sample.objects.filter(population__experiment=self.experiment).count())
        self.assertEqual(2, MutationCall.objects.filter(sample=sample).count())

    def test_a_generated_vcf_with_nothing_left_out_says_nothing_about_it(self):
        import io
        gd_text = "#=GENOME_DIFF\t1.0\nSNP\t1\t.\ttest_ref\t100\tA\n"
        sample, _count, _replaced = import_document_as_sample(
            _parse_document(io.BytesIO(gd_text.encode())), "from_gd", self.context)
        self.assertNotIn("##mutint_omitted", vcf_export.export_vcf_text(sample))

    def test_an_added_mutation_is_regenerated_and_the_file_says_so(self):
        """The export must not quietly hand back a file that is not what was uploaded."""
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"]))
        sample = Sample.objects.get(population__experiment=self.experiment)

        # A mutation added after import: no stored line, so it has to be rebuilt.
        added = Mutation.objects.create(
            experiment=self.experiment, seq_id="test_ref", start_position=140,
            mutation_type="SNP", sequence_change="C", gene="x",
            supplemental_data=Mutation.genome_diff_container(
                {"type": "SNP", "seq_id": "test_ref", "position": 140, "new_seq": "C"}))
        MutationCall.objects.create(sample=sample, mutation=added, present=True,
                                    source="manual", frequency=1.0)

        text = vcf_export.export_vcf_text(sample)
        self.assertIn("##mutint_regenerated=1", text)
        self.assertIn("\t140\t", text)

    def test_the_export_route_refuses_a_sample_you_cannot_see(self):
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"]))
        sample = Sample.objects.get(population__experiment=self.experiment)

        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(404,
                         self.client.get("/import/vcf/%d/export" % sample.pk).status_code)

        self.client.force_login(self.user)
        self.assertEqual(200,
                         self.client.get("/import/vcf/%d/export" % sample.pk).status_code)

    def test_the_gd_export_route_now_checks_too(self):
        # It had no authorization at all; a .gd for any sample id was downloadable.
        self._import(vcf_text(["test_ref\t100\t.\tT\tA\t50\tPASS\t.\tGT\t1"]))
        sample = Sample.objects.get(population__experiment=self.experiment)

        stranger = User.objects.create(username="stranger", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(404,
                         self.client.get("/import/gd/%d/export" % sample.pk).status_code)


IS_ELEMENT = "TTGGCCAATTCCGGAATTCCGGAATTCCGGAATTGGCCAA"


def gff3_with_repeat(sequence, repeats):
    """A reference whose annotation carries `repeat_region` features.

    `breseq_fixture.gff3_text` writes genes only, and MOB inference reads
    `AnnotatedSequence.repeat_locations` -- so this is the fixture that can exercise it.
    """
    lines = ["##gff-version 3",
             "##sequence-region\ttest_ref\t1\t%d" % len(sequence)]
    for index, (start, end, name) in enumerate(repeats):
        lines.append("\t".join([
            "test_ref", "mutint", "repeat_region", str(start), str(end), ".", "+", ".",
            "ID=r%d;Name=%s" % (index, name)]))
    lines.append("##FASTA")
    lines.append(breseq_fixture.fasta_text([("test_ref", sequence)]).rstrip("\n"))
    return "\n".join(lines) + "\n"


class MobInferenceTestCase(TestCase):
    """Promoting an INS to a MOB, which is off by default and stays silent when unsure.

    A wrong MOB is worse than an honest INS: it asserts a mechanism the data may not support,
    and because it lands in the same `get_or_create` key it forks the row against every other
    import of the same call. So these tests are mostly about it *not* firing.
    """

    def setUp(self):
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.user)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

    def _establish(self, repeats):
        # The IS copy is planted at 201..240 so the annotation names real sequence.
        sequence = (SEQUENCE * 2)[:200] + IS_ELEMENT + (SEQUENCE * 2)[240:400]
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "ref.gff3")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(gff3_with_repeat(sequence, repeats))
        normalized, parsed = reference.normalize_reference(path)
        reference_store.establish_or_check(self.experiment, normalized, parsed)
        self.sequence = sequence
        return prepare_experiment_by_id(self.experiment.id)

    def _insert(self, context, inserted, position=100):
        import io
        anchor = self.sequence[position - 1]
        text = vcf_text(["test_ref\t%d\t.\t%s\t%s\t50\tPASS\t.\tGT\t1"
                         % (position, anchor, anchor + inserted)])
        document = vcf.read(io.StringIO(text), "s1.vcf")
        return vcf_import.convert(document, "s1", self.experiment).records

    def test_off_by_default(self):
        context = self._establish([(201, 240, "IS186")])
        records = self._insert(context, IS_ELEMENT)
        self.assertEqual("INS", records[0]["type"])

    @override_settings(MUTINT_VCF_INFER_MOB=True)
    def test_an_insertion_that_is_an_annotated_repeat_becomes_a_mob(self):
        context = self._establish([(201, 240, "IS186")])
        records = self._insert(context, IS_ELEMENT)

        self.assertEqual("MOB", records[0]["type"])
        self.assertEqual("IS186", records[0]["repeat_name"])
        self.assertEqual(1, records[0]["strand"])

    @override_settings(MUTINT_VCF_INFER_MOB=True)
    def test_two_families_matching_leaves_it_an_insertion(self):
        """Ambiguity is silence. Two candidates and we do not know which, so we say INS."""
        sequence_start = 201
        context = self._establish([(sequence_start, sequence_start + 39, "IS186"),
                                   (sequence_start, sequence_start + 39, "IS999")])
        records = self._insert(context, IS_ELEMENT)
        self.assertEqual("INS", records[0]["type"])

    @override_settings(MUTINT_VCF_INFER_MOB=True)
    def test_a_partial_match_leaves_it_an_insertion(self):
        context = self._establish([(201, 240, "IS186")])
        records = self._insert(context, IS_ELEMENT[:20])
        self.assertEqual("INS", records[0]["type"])

    @override_settings(MUTINT_VCF_INFER_MOB=True)
    def test_a_reference_with_no_annotated_repeats_leaves_it_an_insertion(self):
        context = self._establish([])
        records = self._insert(context, IS_ELEMENT)
        self.assertEqual("INS", records[0]["type"])

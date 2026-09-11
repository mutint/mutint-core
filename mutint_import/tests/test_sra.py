"""What an SRA accession means once ENA has answered.

Pure, so these are the cheap tests, and they pin the rules most likely to be got wrong by
accident: which prefix is which kind, how a report row becomes files, which runs are one
sample, and what that sample is then called.
"""

from django.test import SimpleTestCase

from mutint_import import sra
from mutint_import.accessions import AccessionError


def _row(run="SRR2584863", sample="SAMN04096083", alias="REL768A", paired=True,
         experiment="SRX1317390", study="PRJNA295606", title="Microbe sample"):
    base = "ftp.sra.ebi.ac.uk/vol1/fastq/SRR258/003/%s/%s" % (run, run)
    if paired:
        paths = "%s_1.fastq.gz;%s_2.fastq.gz" % (base, base)
        md5s = "4a1529a4165970302d6d8cb50b6e7a43;33492a66dcc95c3f8fc847b41d2c8ceb"
        sizes = "183292913;191090316"
    else:
        paths, md5s, sizes = base + ".fastq.gz", "ce24d1b77b1a17a6579240d59a9fe8cc", "516560356"
    return {
        "run_accession": run, "sample_accession": sample,
        "secondary_sample_accession": "SRS1108291", "experiment_accession": experiment,
        "study_accession": study, "sample_alias": alias, "sample_title": title,
        "library_layout": "PAIRED" if paired else "SINGLE",
        "fastq_ftp": paths, "fastq_md5": md5s, "fastq_bytes": sizes,
    }


class KindTestCase(SimpleTestCase):
    def test_every_accepted_prefix_routes_to_its_kind(self):
        for token, expected in (
                ("SRR2584863", sra.RUN), ("ERR123", sra.RUN), ("DRR9", sra.RUN),
                ("SRX1317390", sra.EXPERIMENT), ("ERX1", sra.EXPERIMENT),
                ("SRS1108291", sra.SAMPLE), ("SAMN04096083", sra.SAMPLE),
                ("SAMEA1234", sra.SAMPLE), ("SAMD00001", sra.SAMPLE),
                ("SRP064605", sra.STUDY), ("PRJNA295606", sra.STUDY),
                ("PRJEB1", sra.STUDY), ("PRJDB2", sra.STUDY),
                ("srr2584863", sra.RUN)):
            self.assertEqual(sra.kind(token), expected, token)

    def test_an_unrecognised_shape_is_refused_naming_the_kinds(self):
        for token in ("NC_000913.3", "REL606", "SRR", "SRR12x", "", "GCF_000005845.2"):
            with self.assertRaises(AccessionError) as caught:
                sra.kind(token)
            self.assertIn("run", str(caught.exception))
            self.assertIn("study", str(caught.exception))


class RowTestCase(SimpleTestCase):
    def test_a_row_becomes_a_run_with_its_files_in_order(self):
        run = sra.run_from_row(_row())
        self.assertEqual(run.accession, "SRR2584863")
        self.assertEqual(run.filenames, ["SRR2584863_1.fastq.gz", "SRR2584863_2.fastq.gz"])
        self.assertEqual(run.files[0]["md5"], "4a1529a4165970302d6d8cb50b6e7a43")
        self.assertEqual(run.files[1]["bytes"], 191090316)
        self.assertEqual(run.bytes, 183292913 + 191090316)
        self.assertEqual(run.sample_accession, "SAMN04096083")
        self.assertEqual(run.alias, "REL768A")
        self.assertEqual(run.layout, "PAIRED")

    def test_the_url_gets_a_scheme_and_the_name_is_the_basename(self):
        run = sra.run_from_row(_row(paired=False))
        self.assertEqual(run.files[0]["url"],
                         "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR258/003/SRR2584863/"
                         "SRR2584863.fastq.gz")
        self.assertEqual(run.filenames, ["SRR2584863.fastq.gz"])

    def test_a_row_with_no_fastq_is_a_run_with_no_files(self):
        row = _row()
        row["fastq_ftp"] = row["fastq_md5"] = row["fastq_bytes"] = ""
        run = sra.run_from_row(row)
        self.assertEqual(run.files, [])
        self.assertEqual(run.accession, "SRR2584863")

    def test_mismatched_column_lengths_are_refused(self):
        # A file without its checksum could not be verified, which is the reason the checksum
        # is asked for at all.
        row = _row()
        row["fastq_md5"] = "4a1529a4165970302d6d8cb50b6e7a43"
        with self.assertRaises(AccessionError) as caught:
            sra.run_from_row(row)
        self.assertIn("SRR2584863", str(caught.exception))

    def test_a_filename_that_could_not_be_a_path_is_refused(self):
        row = _row(paired=False)
        row["fastq_ftp"] = "ftp.sra.ebi.ac.uk/vol1/fastq/bad name.fastq.gz"
        with self.assertRaises(AccessionError):
            sra.run_from_row(row)
        self.assertEqual(sra.safe_filename("host/a/SRR1.fastq.gz"), "SRR1.fastq.gz")

    def test_a_plan_round_trips_through_its_dict(self):
        plan = sra.Plan("SAMN04096083", sra.SAMPLE,
                        [sra.run_from_row(_row()), sra.run_from_row(_row(run="SRR2589046"))])
        again = sra.Plan.from_dict(plan.as_dict())
        self.assertEqual(again.typed, "SAMN04096083")
        self.assertEqual(again.kind, sra.SAMPLE)
        self.assertEqual(again.run_accessions, ["SRR2584863", "SRR2589046"])
        self.assertEqual(again.filenames, plan.filenames)
        self.assertEqual(again.total_bytes, plan.total_bytes)
        self.assertEqual(again.runs[1].files, plan.runs[1].files)
        self.assertEqual(sra.as_plans([plan.as_dict()])[0].typed, "SAMN04096083")

    def test_a_plan_can_be_restricted_to_some_of_its_runs(self):
        plan = sra.Plan("SRP1", sra.STUDY,
                        [sra.run_from_row(_row()), sra.run_from_row(_row(run="SRR2589046"))])
        self.assertEqual(plan.restricted_to(["SRR2589046"]).run_accessions, ["SRR2589046"])
        self.assertEqual(plan.restricted_to(["SRR2589046"]).typed, "SRP1")


class SamplesTestCase(SimpleTestCase):
    def test_a_sample_accession_s_runs_are_one_sample(self):
        plan = sra.Plan("SAMN04096083", sra.SAMPLE,
                        [sra.run_from_row(_row()), sra.run_from_row(_row(run="SRR2589046"))])
        samples = sra.samples_in([plan])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].typed, "SAMN04096083")
        self.assertEqual(samples[0].biosample, "SAMN04096083")
        self.assertEqual(samples[0].alias, "REL768A")
        self.assertEqual([run.accession for run in samples[0].runs],
                         ["SRR2584863", "SRR2589046"])

    def test_a_run_and_an_experiment_are_one_sample_each(self):
        plans = [sra.Plan("SRR2584863", sra.RUN, [sra.run_from_row(_row())]),
                 sra.Plan("SRX9", sra.EXPERIMENT, [sra.run_from_row(_row(run="SRR9"))])]
        self.assertEqual([sample.typed for sample in sra.samples_in(plans)],
                         ["SRR2584863", "SRX9"])

    def test_a_study_is_one_sample_per_biosample_in_order_seen(self):
        plan = sra.Plan("SRP064605", sra.STUDY, [
            sra.run_from_row(_row(run="SRR1", sample="SAMN2", alias="REL1158A")),
            sra.run_from_row(_row(run="SRR2", sample="SAMN1", alias="REL768A")),
            sra.run_from_row(_row(run="SRR3", sample="SAMN2", alias="REL1158A")),
        ])
        samples = sra.samples_in([plan])
        self.assertEqual([sample.biosample for sample in samples], ["SAMN2", "SAMN1"])
        self.assertEqual([run.accession for run in samples[0].runs], ["SRR1", "SRR3"])
        self.assertEqual(samples[1].alias, "REL768A")
        self.assertEqual({sample.typed for sample in samples}, {"SRP064605"})

    def test_a_study_run_with_no_biosample_is_its_own_sample(self):
        plan = sra.Plan("SRP1", sra.STUDY, [sra.run_from_row(_row(run="SRR1", sample=""))])
        self.assertEqual(sra.samples_in([plan])[0].biosample, "SRR1")


class NamingTestCase(SimpleTestCase):
    def _sample(self, typed="SRR2584863", kind=sra.RUN, biosample="SAMN04096083",
                alias="REL768A"):
        return sra.SraSample(typed, kind, biosample, alias, "", [])

    def test_the_alias_names_the_sample(self):
        self.assertEqual(sra.sample_name_for(self._sample()), "REL768A")

    def test_a_blank_alias_falls_back_to_the_accession_typed(self):
        self.assertEqual(sra.sample_name_for(self._sample(alias="  ")), "SRR2584863")
        self.assertEqual(
            sra.sample_name_for(self._sample(typed="SAMN04096083", kind=sra.SAMPLE, alias="")),
            "SAMN04096083")

    def test_an_unusable_alias_falls_back_and_the_consumer_decides_usable(self):
        # The alias is not rewritten into something usable: a name somebody else typed and a
        # name invented from it are two different claims.
        sample = self._sample(alias="E. coli / REL606")
        self.assertEqual(sra.sample_name_for(sample, usable=lambda name: "/" not in name),
                         "SRR2584863")
        self.assertEqual(sra.sample_name_for(sample), "E. coli / REL606")

    def test_a_study_member_falls_back_to_its_biosample(self):
        sample = self._sample(typed="SRP064605", kind=sra.STUDY, alias="")
        self.assertEqual(sra.sample_name_for(sample), "SAMN04096083")


class FilenamesTestCase(SimpleTestCase):
    def test_every_file_is_mapped_to_its_run(self):
        plans = [sra.Plan("SRR2584863", sra.RUN, [sra.run_from_row(_row())]),
                 sra.Plan("SRR9", sra.RUN, [sra.run_from_row(_row(run="SRR9", paired=False))])]
        self.assertEqual(sra.filenames_by_run(plans), {
            "SRR2584863_1.fastq.gz": "SRR2584863",
            "SRR2584863_2.fastq.gz": "SRR2584863",
            "SRR9.fastq.gz": "SRR9",
        })

    def test_one_name_from_two_runs_is_refused(self):
        first = sra.run_from_row(_row(run="SRR1", paired=False))
        second = sra.run_from_row(_row(run="SRR2", paired=False))
        second.files[0]["name"] = "SRR1.fastq.gz"
        with self.assertRaises(AccessionError) as caught:
            sra.filenames_by_run([sra.Plan("SRR1", sra.RUN, [first]),
                                  sra.Plan("SRR2", sra.RUN, [second])])
        self.assertIn("SRR1.fastq.gz", str(caught.exception))

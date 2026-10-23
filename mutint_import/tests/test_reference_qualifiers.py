"""What a GenBank says beyond what breseq annotates from survives the stored GFF3.

The model keeps every qualifier it does not interpret in `Feature.attributes`, every
feature type the file carries, and the record's header in `AnnotatedSequence.metadata`;
the stored GFF3 carries all of it and the GenBank export writes it back. Two things are
rebuilt rather than stored -- the `gene` feature over each locus and `/translation` -- and
both are checked here against the file they came from.
"""

import os
import shutil
import tempfile

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from django.test import SimpleTestCase

from mutint_import import reference, reference_export
from mutint_import.annotate import genbank, gff3

CHR = "chr"

# 10..60 is a clean CDS: M then fifteen A, with its stop. 70..120 has a stop inside it, as a
# ribosomal-slippage CDS reads in one frame.
CLEAN_CDS = "ATG" + "GCT" * 15 + "TAA"
SLIPPERY_CDS = "ATG" + "GCT" * 5 + "TAA" + "GCT" * 9 + "TAA"
SEQUENCE = ("A" * 9 + CLEAN_CDS + "C" * 9 + SLIPPERY_CDS + "ACGT" * 70)
CLEAN_PROTEIN = "M" + "A" * 15

assert len(CLEAN_CDS) == 51 and len(SLIPPERY_CDS) == 51 and len(SEQUENCE) == 400


def _feature(start_1, end_1, strand, feature_type, qualifiers):
    return SeqFeature(FeatureLocation(start_1 - 1, end_1, strand=strand), type=feature_type,
                      qualifiers=qualifiers)


def _record():
    """A record with the shapes NCBI files carry: a header, a `source`, a gene+CDS locus
    with every common qualifier, two RNAs, a regulatory element, a misc_feature, a mobile
    element and a gene with no feature under it."""
    record = SeqRecord(Seq(SEQUENCE), id=CHR, name=CHR,
                       description="Escherichia coli K-12 test genome")
    record.annotations.update({
        "molecule_type": "DNA", "topology": "circular", "organism": "Escherichia coli",
        "source": "Escherichia coli", "taxonomy": ["Bacteria", "Proteobacteria"],
        "comment": "Annotated using prokka.\nThen transferred.", "date": "24-JUL-2023",
    })
    record.features = [
        _feature(1, 400, 1, "source", {"organism": ["Escherichia coli"],
                                        "mol_type": ["genomic DNA"], "strain": ["K-12"],
                                        "db_xref": ["taxon:562"]}),
        _feature(10, 60, 1, "gene", {"gene": ["thrA"], "locus_tag": ["b0001"],
                                      "old_locus_tag": ["ECK0001"], "db_xref": ["GeneID:1"],
                                      "gene_synonym": ["thr-1"]}),
        _feature(10, 60, 1, "CDS", {
            "gene": ["thrA"], "locus_tag": ["b0001"], "old_locus_tag": ["ECK0001"],
            "EC_number": ["2.7.2.4"],
            "inference": ["COORDINATES: similar to AA sequence:RefSeq:WP_1"],
            "GO_function": ["GO:1 - a [Evidence IEA]", "GO:2 - b [Evidence IEA]"],
            "note": ["first note", "second, with a comma"], "codon_start": ["1"],
            "transl_table": ["11"], "product": ["aspartokinase"], "protein_id": ["WP_1.1"],
            "db_xref": ["GeneID:1", "UniProtKB:P1"], "translation": [CLEAN_PROTEIN],
            "accession": ["X1"]}),
        _feature(70, 120, 1, "gene", {"locus_tag": ["b0002"]}),
        _feature(70, 120, 1, "CDS", {
            "locus_tag": ["b0002"], "codon_start": ["1"], "transl_table": ["11"],
            "product": ["slippery protein"], "ribosomal_slippage": [None],
            "translation": ["MAAAAA"]}),
        _feature(130, 205, 1, "gene", {"locus_tag": ["b0003"]}),
        _feature(130, 205, 1, "tRNA", {"locus_tag": ["b0003"], "product": ["tRNA-Thr"],
                                        "anticodon": ["(pos:163..165,aa:Thr,seq:tgt)"]}),
        _feature(210, 240, 1, "regulatory", {"regulatory_class": ["riboswitch"],
                                             "note": ["FMN riboswitch"]}),
        _feature(250, 280, -1, "misc_feature", {"locus_tag": ["b0004"],
                                                "note": ["pseudoknot"], "pseudo": [None]}),
        _feature(290, 330, 1, "gene", {"gene": ["ssrA"], "locus_tag": ["b0005"]}),
        _feature(290, 330, 1, "tmRNA", {"gene": ["ssrA"], "locus_tag": ["b0005"],
                                         "product": ["tmRNA"]}),
        _feature(340, 380, 1, "mobile_element", {
            "mobile_element_type": ["insertion sequence:IS1"], "note": ["IS1 copy"]}),
        _feature(385, 395, 1, "gene", {"locus_tag": ["b0006"], "note": ["orphan gene"]}),
    ]
    return record


def _qualifiers(bio_feature):
    return {key: list(values) for key, values in bio_feature.qualifiers.items()}


class _Fixture(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gbk = os.path.join(self.tmp, "in.gbk")
        SeqIO.write([_record()], self.gbk, "genbank")
        self.refs = genbank.load_genbank(self.gbk)
        self.text = gff3.render_breseq_gff3(self.refs)
        self.reloaded = gff3.load_gff3(self._write("stored.gff3", self.text))

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def _rows(self, text=None):
        return [line for line in (text or self.text).split("##FASTA")[0].splitlines()
                if line and not line.startswith("#")]

    def _row(self, needle, text=None):
        rows = [row for row in self._rows(text) if needle in row]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def _export(self, refs=None):
        text = reference_export.render(refs or self.reloaded, "genbank")
        path = self._write("out.gbk", text)
        return text, list(SeqIO.parse(path, "genbank"))[0]

    def _by_type(self, record):
        return {(f.type, int(f.location.start), int(f.location.end)): f
                for f in record.features}


class ModelTestCase(_Fixture):
    def test_the_uninterpreted_qualifiers_ride_on_the_feature(self):
        cds = next(f for f in self.refs[CHR].features if f.locus_tag == "b0001")
        self.assertEqual(cds.attributes["db_xref"], ["GeneID:1", "UniProtKB:P1"])
        self.assertEqual(cds.attributes["note"], ["first note", "second, with a comma"])
        self.assertEqual(cds.attributes["codon_start"], ["1"])
        # What fed a field is not repeated; the translation is never kept.
        for key in ("gene", "locus_tag", "product", "transl_table", "translation"):
            self.assertNotIn(key, cds.attributes)

    def test_a_gene_is_folded_into_the_feature_under_it(self):
        types = [f.type for f in self.refs[CHR].features]
        self.assertEqual(types.count("gene"), 1, types)  # the orphan alone
        cds = next(f for f in self.refs[CHR].features if f.locus_tag == "b0001")
        self.assertEqual(cds.attributes["gene_synonym"], ["thr-1"])  # the gene's alone
        self.assertEqual(cds.attributes["db_xref"], ["GeneID:1", "UniProtKB:P1"])  # the CDS's

    def test_other_feature_types_are_kept_and_not_annotated_against(self):
        contig = self.refs[CHR]
        self.assertEqual(sorted(f.type for f in contig.features),
                         ["CDS", "CDS", "gene", "misc_feature", "mobile_element",
                          "regulatory", "tRNA", "tmRNA"])
        # tmRNA and ncRNA are annotated against like tRNA; the rest are kept for the file.
        self.assertEqual(sorted(l.feature.type for l in contig.gene_locations),
                         ["CDS", "CDS", "tRNA", "tmRNA"])
        regulatory = next(f for f in contig.features if f.type == "regulatory")
        self.assertEqual(regulatory.attributes["regulatory_class"], ["riboswitch"])
        self.assertEqual(regulatory.name, "")

    def test_the_header_and_source_are_the_contigs_metadata(self):
        metadata = self.refs[CHR].metadata
        self.assertEqual(metadata["Note"], ["Escherichia coli K-12 test genome"])
        self.assertEqual(metadata["organism"], ["Escherichia coli"])
        self.assertEqual(metadata["strain"], ["K-12"])
        self.assertEqual(metadata["db_xref"], ["taxon:562"])
        self.assertEqual(metadata["taxonomy"], ["Bacteria", "Proteobacteria"])
        self.assertEqual(metadata["comment"], ["Annotated using prokka. Then transferred."])
        self.assertIs(self.refs[CHR].circular, True)


class StoredGff3TestCase(_Fixture):
    def test_extra_attributes_follow_breseqs_sorted_and_comma_joined(self):
        row = self._row("Alias=b0001")
        attributes = row.split("\t")[8]
        keys = [item.partition("=")[0] for item in attributes.split(";")]
        self.assertEqual(keys[:4], ["Alias", "ID", "Name", "Note"])
        extras = keys[keys.index("transl_table") + 1:]
        self.assertEqual(extras, sorted(extras))
        self.assertIn("db_xref=GeneID:1,UniProtKB:P1", attributes)
        self.assertIn("note=first note,second%2C with a comma", attributes)
        self.assertEqual(gff3.parse_attributes(attributes)["note"],
                         ["first note", "second, with a comma"])

    def test_a_key_a_loader_interprets_is_not_stored(self):
        self.assertNotIn("accession=", self.text)
        self.assertNotIn("translation=", self.text)

    def test_a_bare_qualifier_is_an_empty_value(self):
        self.assertIn("ribosomal_slippage=", self._row("Alias=b0002"))

    def test_other_types_and_the_orphan_gene_are_rows(self):
        self.assertIn("\tregulatory\t", self.text)
        self.assertIn("\ttmRNA\t", self.text)
        self.assertIn("Pseudo=true", self._row("\tmisc_feature\t"))
        self.assertIn("Alias=b0006", self._row("\tgene\t"))
        self.assertEqual(len([r for r in self._rows() if "\tgene\t" in r]), 1)

    def test_the_region_row_carries_the_header(self):
        row = self._row("\tregion\t")
        attributes = gff3.parse_attributes(row.split("\t")[8])
        self.assertEqual(attributes["Is_circular"], ["true"])
        self.assertEqual(attributes["Note"], ["Escherichia coli K-12 test genome"])
        self.assertEqual(attributes["strain"], ["K-12"])
        self.assertEqual(attributes["taxonomy"], ["Bacteria", "Proteobacteria"])
        self.assertNotIn("%0A", row)

    def test_the_stored_form_is_a_fixed_point(self):
        self.assertEqual(gff3.render_breseq_gff3(self.reloaded), self.text)

    def test_reloading_gives_the_model_back(self):
        contig = self.reloaded[CHR]
        self.assertEqual(contig.metadata, self.refs[CHR].metadata)
        for before, after in zip(self.refs[CHR].features, contig.features):
            self.assertEqual((before.type, before.name, before.locus_tag, before.product,
                              before.pseudogene, before.attributes),
                             (after.type, after.name, after.locus_tag, after.product,
                              after.pseudogene, after.attributes))

    def test_metadata_alone_makes_a_region_row_and_leaves_topology_unknown(self):
        record = _record()
        del record.annotations["topology"]
        path = os.path.join(self.tmp, "flat.gbk")
        SeqIO.write([record], path, "genbank")
        refs = genbank.load_genbank(path)
        text = gff3.render_breseq_gff3(refs)
        row = self._row("\tregion\t", text)
        self.assertNotIn("Is_circular", row)
        reloaded = gff3.load_gff3(self._write("flat.gff3", text))
        self.assertIsNone(reloaded[CHR].circular)
        self.assertEqual(reloaded[CHR].metadata["strain"], ["K-12"])
        self.assertEqual(reference.gff3_topologies(text), {})

    def test_with_topology_keeps_the_rest_of_the_region_row(self):
        flipped = reference.with_topology(self.text, {CHR: False})
        self.assertEqual(reference.gff3_topologies(flipped), {CHR: False})
        reloaded = gff3.load_gff3(self._write("flipped.gff3", flipped))
        self.assertEqual(reloaded[CHR].metadata, self.refs[CHR].metadata)
        self.assertEqual(gff3.render_breseq_gff3(reloaded), flipped)
        cleared = reference.with_topology(self.text, {})
        self.assertEqual(reference.gff3_topologies(cleared), {})
        self.assertIn("Note=Escherichia coli K-12 test genome", self._row("\tregion\t", cleared))


class ExportTestCase(_Fixture):
    def test_every_feature_comes_back_with_its_qualifiers(self):
        _, record = self._export()
        before = self._by_type(SeqIO.parse(self.gbk, "genbank").__next__())
        after = self._by_type(record)
        self.assertEqual(sorted(after), sorted(before))
        for key, feature in before.items():
            expected = {k: v for k, v in _qualifiers(feature).items()
                        if k not in ("translation", "accession")}
            # A gene's qualifiers are folded onto the feature under it, so a child carries
            # what its gene had and it lacked (thrA's `gene_synonym`).
            gene = before.get(("gene",) + key[1:])
            if key[0] != "gene" and gene is not None:
                for k, v in _qualifiers(gene).items():
                    expected.setdefault(k, v)
            got = {k: v for k, v in _qualifiers(after[key]).items() if k != "translation"}
            if key[0] == "gene" and key[1] != 384:
                # Synthesized from the child: the NCBI subset, and the child's db_xref.
                self.assertEqual(got.get("locus_tag"), expected.get("locus_tag"), key)
                self.assertEqual(got.get("gene"), expected.get("gene"), key)
                self.assertEqual(got.get("old_locus_tag"), expected.get("old_locus_tag"), key)
                continue
            self.assertEqual(got, expected, key)

    def test_the_translation_is_regenerated_or_withheld(self):
        _, record = self._export()
        after = self._by_type(record)
        self.assertEqual(after[("CDS", 9, 60)].qualifiers["translation"], [CLEAN_PROTEIN])
        self.assertNotIn("translation", after[("CDS", 69, 120)].qualifiers)

    def test_the_header_comes_back(self):
        text, record = self._export()
        self.assertEqual(record.description, "Escherichia coli K-12 test genome")
        self.assertEqual(record.annotations["organism"], "Escherichia coli")
        self.assertEqual(record.annotations["taxonomy"], ["Bacteria", "Proteobacteria"])
        self.assertEqual(record.annotations["date"], "24-JUL-2023")
        self.assertEqual(record.annotations["topology"], "circular")
        self.assertIn("Annotated using prokka.", record.annotations["comment"])
        source = record.features[0]
        self.assertEqual(source.type, "source")
        self.assertEqual(_qualifiers(source), {
            "organism": ["Escherichia coli"], "mol_type": ["genomic DNA"],
            "strain": ["K-12"], "db_xref": ["taxon:562"]})

    def test_a_bare_qualifier_is_bare_again(self):
        text, _ = self._export()
        self.assertIn("                     /ribosomal_slippage\n", text)

    def test_a_record_that_said_nothing_still_exports_as_before(self):
        record = SeqRecord(Seq(SEQUENCE), id=CHR, name=CHR, description="")
        record.annotations["molecule_type"] = "DNA"
        record.features = [_feature(10, 60, 1, "CDS", {"locus_tag": ["b0001"],
                                                        "product": ["aspartokinase"]})]
        path = os.path.join(self.tmp, "bare.gbk")
        SeqIO.write([record], path, "genbank")
        refs = genbank.load_genbank(path)
        self.assertEqual(refs[CHR].metadata, {})
        self.assertNotIn("\tregion\t", gff3.render_breseq_gff3(refs))
        text, exported = self._export(refs)
        self.assertEqual([f.type for f in exported.features], ["gene", "CDS"])
        self.assertEqual(exported.description, CHR)

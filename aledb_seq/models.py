from django.db import models
from aledb_common.util import get_gene_list
from aledb_seq.util import get_ecocyc_gene_list
from django.utils.safestring import mark_safe

blank_field = {"blank": True, "null": True}


# TODO: Refactor: figure out how to get a ResequencingExperiment to return its list of observed mutations and remove functionality from aledb_seq.views.common
class ResequencingExperiment(models.Model):
    tech_rep = models.ForeignKey("aledb_experiment.TechnicalReplicate", on_delete=models.CASCADE,
                                 null=True)
    person = models.CharField(max_length=200,
                              blank=True)
    reads = models.IntegerField(blank=True,
                                default=0)
    average_read_length = models.FloatField(blank=True,
                                            default=0)
    mutations = models.ManyToManyField("Mutation",
                                       through="ObservedMutation")
    sample_name = models.CharField(max_length=200,
                                blank=True,
                                null=True)
    mean_coverage = models.FloatField(blank=True,
                                      default=0)
    percentage_mapped = models.FloatField(blank=True,
                                          default=0)
    # Whether this sample's alignment lives in the managed store. Deliberately a flag and
    # not a path: the location is derived from this row's pk by aledb_common.store. Three
    # per-row path columns used to live here -- location, experiment_location,
    # gatk_location -- pointing into a bring-your-own breseq tree. They were only ever used
    # to build breseq HTML report URLs, and went with that feature.
    bam_stored = models.BooleanField(default=False)
    # Whether the coverage BigWig derived from that alignment is in the store. A second flag
    # rather than something inferred from bam_stored: the derivation needs external tools and
    # is best-effort, so a sample can have its reads and not its coverage.
    coverage_stored = models.BooleanField(default=False)

    @property
    def ale_experiment(self):
        return self.tech_rep.isolate.flask.ale_id.ale_experiment

    @property
    def ale_id(self):
        return self.tech_rep.isolate.flask.ale_id.ale_id

    @property
    def flask_number(self):
        return self.tech_rep.isolate.flask.flask_number

    @property
    def ale_flask_isolate_str(self):

        if self.tech_rep.isolate.description is not None:

            if len(self.tech_rep.isolate.description) > 0:

                return self.tech_rep.isolate.description

        return u"A%d F%d I%d R%d" % (self.ale_id,
                                     self.flask_number,
                                     self.tech_rep.isolate.isolate_number,
                                     self.tech_rep.tech_rep_number)

    @property
    def exp_ale_flask_isolate_str(self):
        return self.ale_experiment.name + " " + self.ale_flask_isolate_str


class UnassignedMissingCoverageEvidence(models.Model):
    """An MC evidence record from the GenomeDiff.

    Eight further columns -- reads_left_url, reads_right_url, coverage, size, reads_left,
    reads_right, gene, description -- were scraped out of breseq's index.html and attached
    here. Nothing ever read any of them, and they went with the HTML report support. These
    three come from the GenomeDiff itself, so the count on the stats page still works.
    """

    seq_id = models.CharField(max_length=100)
    start = models.CharField(max_length=100)
    end = models.CharField(max_length=100)
    sequencing_experiment = models.ForeignKey(ResequencingExperiment,
                                              on_delete=models.CASCADE)


class Mutation(models.Model):
    mutation_type = models.CharField(max_length=3,
                                     null=True,
                                     help_text="""Use breseq mutation codes, see the genome diff site
                                     on the barrick lab wiki (http://tinyurl.com/l3fvnap) for more
                                     information""")
    position = models.IntegerField()
    feature_length = models.IntegerField(blank=True,
                                         null=True)
    sequence_change = models.CharField(max_length=200)
    protein_change = models.CharField(max_length=300,
                                      default="")
    gene = models.CharField(max_length=19000, blank=True, null=True)  # TODO: use TextField for this.
    product = models.TextField(default="", null=True)
    function = models.CharField(max_length=500, default="", null=True)
    go_process = models.CharField(max_length=300, default="", null=True)
    go_component = models.CharField(max_length=300, default="", null=True)
    reseq_reference = models.CharField(max_length=200, **blank_field)
    tags = models.CharField(max_length=500, **blank_field)

    # Mutations belong to one experiment. Two experiments that call the same
    # variant get their own rows, so re-annotating one against a new reference
    # cannot silently rewrite another's annotation.
    ale_experiment = models.ForeignKey("aledb_experiment.AleExperiment",
                                       related_name="mutations",
                                       on_delete=models.CASCADE, db_index=True,
                                       **blank_field)

    # ---- breseq annotation, generated at import by aledb_import.annotate -----
    #
    # Split by how it is used, not by how it arrived. These six are filtered,
    # counted and sorted on, so they are real columns; snp_type and
    # mutation_category are indexed because that is what replaces
    # aledb_dashboard.util substring-matching the rendered protein_change.
    snp_type = models.CharField(max_length=100, db_index=True, **blank_field)
    mutation_category = models.CharField(max_length=100, db_index=True, **blank_field)
    gene_name = models.TextField(**blank_field)
    locus_tag = models.TextField(**blank_field)
    start_position = models.IntegerField(**blank_field)
    end_position = models.IntegerField(**blank_field)

    # Everything else breseq annotates -- gene_position, gene_strand, the codon_*
    # and aa_* fields, genes_overlapping/inactivated/promoter and their locus_tag
    # counterparts, transl_table. Nothing queries these; they are read whole, per
    # row, to render a mutation. Keeping them here means a new annotation field
    # needs no migration.
    #
    # Deliberately NOT folded into gd_data: to_gd_line() splats every gd_data key
    # onto the line it emits for gdtools APPLY, and display markup has no place
    # there.
    annotation = models.JSONField(**blank_field)

    # "reference_error" was created to indicate mutations that are generated only because
    # the reference isn't realistic and not because the organism is actually
    # different from the original strain. This is why setting this value to
    # true ignores enables further analysis to ignore these mutations.

    reference_error = models.BooleanField(default=False)

    # Verbatim parsed GenomeDiff mutation record (type, id, parent_ids and all
    # type-specific + optional key=value fields), stored losslessly so a mutation
    # can be round-tripped back to a .gd line for gdtools APPLY. Null for mutations
    # imported before this field existed (e.g. via the breseq-directory CLI path).
    gd_data = models.JSONField(**blank_field)

    def __unicode__(self):
        return u"%d %s" % (self.position,
                           self.sequence_change)

    def to_gd_line(self) -> str:
        """Reconstruct this mutation's GenomeDiff line (for gdtools APPLY).

        Prefers the verbatim ``gd_data``; falls back to a best-effort line built
        from the scalar columns for legacy rows where ``gd_data`` is null (that
        fallback is not guaranteed APPLY-complete — the discrete alleles were not
        captured for those rows)."""
        from genomediff.records import Record, TYPE_SPECIFIC_FIELDS

        if self.gd_data:
            data = dict(self.gd_data)
            record_type = data.pop('type', self.mutation_type)
            record_id = data.pop('id', self.id)
            parent_ids = data.pop('parent_ids', None)
            return str(Record(record_type, record_id, parent_ids=parent_ids, **data))

        if self.mutation_type not in TYPE_SPECIFIC_FIELDS:
            return ""
        attributes = {'seq_id': self.reseq_reference, 'position': self.position}
        if self.feature_length is not None:
            attributes['size'] = self.feature_length
        return str(Record(self.mutation_type, self.id, parent_ids=None, **attributes))

    #: The K-12 MG1655 accession EcoCyc's gene pages are keyed on. Matched on the accession
    #: rather than the exact string so a versioned name -- NC_000913.3, which is what RefSeq
    #: actually distributes -- is still recognised. Exact equality was fine while a contig
    #: name could never change; renaming an experiment's sequences makes it a live trap,
    #: because re-establishing the reference from a RefSeq download would silently turn every
    #: EcoCyc link off.
    ECOCYC_ACCESSION = 'NC_000913'

    def is_ecocyc_gene(self) -> bool:
        name = self.reseq_reference or ''
        return name == self.ECOCYC_ACCESSION or name.startswith(self.ECOCYC_ACCESSION + '.')

    def ecocyc_gene_urls(self) -> str:
        """
        get gene links string for table cell display
        :return: gene links for display in mutation table
        """
        return mark_safe(", ".join(get_ecocyc_gene_list(get_gene_list(self.gene), self.is_ecocyc_gene())))


class ObservedMutation(models.Model):
    sequencing_experiment = models.ForeignKey(ResequencingExperiment, on_delete=models.CASCADE, null=True)
    # make sure not delete mutation if there is associated observed mutations
    mutation = models.ForeignKey(Mutation, on_delete=models.DO_NOTHING)
    # Whether the mutation is in this sample. True is an assertion that it is there,
    # False that it was looked for and found absent, null that nothing was recorded. It used
    # to sit beside `breseq_present` and `gatk_present`, one flag per caller, and every read
    # path asked those rather than this -- so a mutation a *person* added, which no caller
    # found, was absent from every cross-sample table. `source` says who asserted it; this
    # says whether it is there.
    present = models.BooleanField(null=True)
    wt_reads = models.IntegerField(null=True)
    mutated_reads = models.IntegerField(null=True)
    other_reads = models.IntegerField(null=True)
    reference_genome_likelihood = models.FloatField(null=True)
    # The one frequency. `frequency_gatk` sat beside it, for the GATK half of a gdtools
    # COMPARE merge that no import path has ever written -- 0 of 74,859 rows had a value.
    # Being always null was not merely useless: `aledb_filter` ANDed a `frequency_gatk__lt`
    # term into the exclusion, and a comparison against null is never true, so the frequency
    # cutoff excluded nothing at all.
    frequency = models.DecimalField(null=True,
                                    max_digits=5,
                                    decimal_places=4)
    # Which caller produced this observation. Imports record "breseq"; other
    # callers can be added alongside. Left null on rows imported before this
    # existed, which came from a gdtools COMPARE merge of breseq and
    # GATK/CNVnator, so "breseq" would misrepresent them.
    source = models.CharField(max_length=50, db_index=True, blank=True, null=True)

    def get_experiment_id(self):
        return self.sequencing_experiment.tech_rep.isolate.flask.ale_id.ale_experiment_id



class ExperimentReference(models.Model):
    """The single reference genome shared by every sample in an experiment.

    breseq writes `data/reference.gff3` and `data/reference.fasta` alongside each run, so the
    reference arrives with the results rather than being uploaded separately. The first
    imported sample establishes it; every later sample must hash-match or be rejected, which
    is what keeps an experiment from silently ending up with mixed references.

    The files themselves live in the managed store, at a path derived from
    `ale_experiment_id` -- see aledb_common.store.
    """

    ale_experiment = models.OneToOneField("aledb_experiment.AleExperiment",
                                          on_delete=models.CASCADE,
                                          related_name="reference")
    # Provenance for the stored artifacts -- "did the files on disk change" -- not identity.
    # `gff3_sha256` embeds the inline ##FASTA, so it is the finest of the three: equal
    # gff3_sha256 means identical genome *and* identical annotation.
    gff3_sha256 = models.CharField(max_length=64)
    fasta_sha256 = models.CharField(max_length=64)
    # Identity: the bases alone, independent of names and order. See
    # aledb_import.reference.sequence_set_digest. Blank on a row whose stored FASTA was
    # missing when the backfill ran -- blank means *unknown*, never "matches".
    sequence_sha256 = models.CharField(max_length=64, blank=True, default="")
    # [{"id": ..., "length": ..., "sha256": ..., "aliases": [...]}, ...] in the order they
    # appear in the FASTA. `sha256` is the per-sequence digest a rename maps old names onto
    # new ones by; `aliases` is the names this sequence used to have, and is what
    # /mutations/reference/<id>/chromalias serves so stored BAMs and BigWigs -- which keep
    # the names they were built with -- still resolve. Absent on both counts until a
    # reference is re-established or renamed.
    seq_ids = models.JSONField(default=list)
    total_length = models.BigIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return "Reference for %s" % (self.ale_experiment_id,)

    def matches_sequence(self, sequence_sha256, fasta_sha256=None):
        """Whether a reference is *the same reference* as this one.

        The bases alone are the invariant -- not their names, not their order, not their
        annotation. Two breseq runs against the same genome can legitimately carry
        annotation that differs in detail, and the same genome can legitimately arrive
        under a different set of contig names; refusing either would refuse valid data.

        A row whose `sequence_sha256` was never computed -- its stored FASTA was missing
        when the backfill ran -- falls back to the old, name-sensitive comparison. That is
        exactly this row's behaviour before the column existed, and `plan_rename` refuses
        to rename against it, because it has no per-sequence hashes to match names on.
        """
        if self.sequence_sha256:
            return self.sequence_sha256 == sequence_sha256
        return fasta_sha256 is not None and self.fasta_sha256 == fasta_sha256

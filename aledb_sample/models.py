from django.db import models

from aledb_experiment import coordinates
from aledb_common.util import GENE_LIST_LIMIT, get_gene_list
from aledb_sample.util import get_ecocyc_gene_list
from django.utils.safestring import mark_safe

blank_field = {"blank": True, "null": True}


# TODO: Refactor: figure out how to get a Sample to return its list of mutation calls and remove functionality from aledb_sample.views.common
class SupplementalDataMixin(models.Model):
    """A JSON column for the records a row arrived with, namespaced by who owns each.

    The supplemental material that came with the thing, in the sense a paper means it: not
    the finding, but everything shipped alongside so somebody can check it::

        {"aledb_core": {"genome_diff": { ...the verbatim breseq record... }}}
        {"aledb_core": {"breseq": {...}, "sequencing": {...}, "curation": {...}}}

    **Two levels, and the outer one is a component rather than a Django app label** -- core
    is fifteen apps, so `aledb_core` names the checkout, the same unit `about_registry` keys
    its entries by and for the same reason. A plugin writes under its own name, so two of
    them cannot collide and neither can collide with core.

    **What belongs here**: something that arrives with an import, shares the row's lifetime,
    and is read whole rather than queried. **What does not**: anything with its own
    lifecycle, anything you want to filter or aggregate on, and anything large -- this column
    is loaded on every read of the row. A plugin storing real state should own a table with a
    plain foreign key; `docs/plugin/derived-data.md` says so and says why.

    `default=dict` rather than nullable: for a container several writers merge into, the
    empty dict is the right zero, and "never written" is the absence of a key rather than a
    null column. The accessors answer `{}` either way, so a caller cannot tell and does not
    need to.

    Shaped like `SoftDeleteMixin` in `aledb_experiment.models` -- an abstract model carrying
    the field and the helpers that belong with it, rather than the same code on two models
    waiting to drift.
    """

    #: The component key core writes under. A plugin passes its own to `set_record`.
    COMPONENT = "aledb_core"

    supplemental_data = models.JSONField(default=dict)

    class Meta:
        abstract = True

    def record(self, kind, component=None):
        """One group, or `{}` -- so a caller never has to guard on an absent key."""
        component = component or self.COMPONENT
        return (self.supplemental_data or {}).get(component, {}).get(kind) or {}

    def set_record(self, component, kind, value, save=True):
        """Merge one record in, leaving every other component's keys alone.

        A shared column's hazard is the lost update: `save()` writes the whole thing, so two
        writers that each read, modify and write can silently drop one another's keys. This
        merges into the container as it stands and saves only this field.
        """
        container = dict(self.supplemental_data or {})
        owned = dict(container.get(component) or {})
        owned[kind] = value
        container[component] = owned
        self.supplemental_data = container
        if save:
            self.save(update_fields=["supplemental_data"])


class Sample(SupplementalDataMixin):
    """One sample: what was sequenced, where it sits in the experiment, and what came back.

    **Three models used to be here** -- `Isolate` held where the sample sat and what it was
    called, `TechnicalReplicate` sat between them holding a number and some tags, and this
    row (`Sample`) held the sequencing. The 1:N between them was structural and never used: only two
    code paths ever created a run, both one per replicate, and re-importing a sample is
    replace-in-place rather than a second row. So the layers cost a four-segment join on
    every query and bought nothing.

    Collapsing them onto *this* row rather than onto `Isolate` is what made the merge
    cheap. `MutationCall.sample` and `MutationEdit.sample` point here, the managed
    store is `<store>/samples/<pk>/` keyed by this pk, and every variable in the suite
    called `reseq` or `sample` already meant this row. Merging the other way would have
    moved all of it -- and the name this model now has was the argument.

    What a replicate was is now a suffix on the label: `3-30000-1-1` and `3-30000-1-2`
    import as two samples named `1-1` and `1-2` under one flask, which is what they always
    were.
    """

    #: Which population the sample was drawn from. Nullable because it always was: a sample
    #: with no chain above it is unreachable from every listing
    #: (`get_ordered_reseq_queryset` filters it out) and the views 404 on it rather than
    #: treating it as ownerless.
    population = models.ForeignKey("aledb_experiment.Population", on_delete=models.CASCADE,
                                   null=True)

    #: When along that population's history it was drawn -- generations, cumulative
    #: divisions, hours; whatever the experiment counts by.
    #:
    #: **This was a `TimePoint` row**, a whole level of the chain whose only columns were
    #: this number, the population it belonged to and a `Media` foreign key. Every sample at
    #: one time point shared the row, which bought a join on every query and one thing
    #: besides: a `unique_together` that made two samples at one time point share a parent
    #: rather than each carrying the number. Nothing needed that.
    #:
    #: A float, where the column was an integer, because the ordinal is whatever the
    #: experiment counts by and half a generation is a thing somebody records. It is still
    #: what aledb-fixation orders by to take a population's last two, and filtering on it is
    #: now `time_point__gte=500` on the sample rather than a join.
    time_point = models.FloatField(**blank_field)

    #: Text, not a number -- see `Population.name`. A clone is named `763A` as often as
    #: `763`, and two clones from one time point differ only in that trailer. Unique within
    #: its time point by convention rather than by constraint; `aledb_experiment.samples`
    #: enforces it on the edit path, and `plan_moves` says why two samples must not share a
    #: coordinate.
    name = models.CharField(max_length=100, default="1")

    #: **Clonal, not mixed** -- the flag was `is_population` and its meaning is inverted.
    #: A clone is one genotype; a mixed sample is a whole evolving population sequenced
    #: together, which is why its mutations carry frequencies and a clone's do not.
    #:
    #: `True` by default because a sample is clonal unless something says otherwise, and
    #: what says otherwise is breseq: an import reads `-p` (polymorphism mode) out of the
    #: `#=COMMAND` line, and that is the only thing that sets this False on the way in.
    is_clonal = models.BooleanField(default=True)

    #: What a person calls this sample. Preferred over the computed coordinate wherever a
    #: sample is labelled -- see `label`.
    description = models.CharField(max_length=300, **blank_field)
    #: Both came from `TechnicalReplicate`.
    tags = models.CharField(max_length=500, **blank_field)
    #: What the file or folder this was imported from was called. `source_name`, because
    #: `sample_name` on a model called `Sample` says nothing about which of its several
    #: names it is -- this is the one the import read, not the one the product displays.
    source_name = models.CharField(max_length=200, blank=True, null=True)
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
    #: Whether breseq's own HTML report -- its `output/` directory -- is in the store, so
    #: `/mutations/report/<pk>/` has something to show. A third flag for the same reason the
    #: second exists: a sample can have its reads and not its report, because a `.gd` drop
    #: carries no report at all and a hand-assembled `data/` folder need not either.
    report_stored = models.BooleanField(default=False)

    # Shortcuts up the chain, for the call sites that want one field from it and not the
    # rows in between. They were `ale_experiment`, `ale_id` and `flask_number`.
    #
    # `population_name` rather than `population`, because it answers a *value* while the row
    # of that name is now a column on this model. `time_point` was the third of these
    # and is gone: the time point **is** a column here, so a property returning it would be
    # a second spelling of one field.

    #: The three groups core keeps in `supplemental_data`, and what each holds.
    #:
    #: **Nine columns stood here** -- `reference_genome`, `sequencing_date`,
    #: `breseq_version`, `library_prep`, `reads`, `average_read_length`, `mean_coverage`,
    #: `percentage_mapped`, `medium_description`. None of them was ever filtered, ordered,
    #: aggregated or joined on anywhere in the suite; they are read whole to render a row and
    #: to build the interop payload, which is the argument `Mutation.annotation` and
    #: `MutationCall.evidence` are already stored on.
    #:
    #: A tenth, `person`, was **deleted rather than moved**. A free-text name is not a user,
    #: and what somebody wants to record about who handled a sample belongs in
    #: `medium_description` or `description` beside everything else descriptive.
    #:
    #: **`source_name` deliberately stayed a column**, though it looks like one of these.
    #: `gd_import._get_or_create_autonumbered_chain` *filters* on it to decide whether a
    #: re-import reuses a sample or allocates a new one, so it is identity rather than
    #: information -- in JSON that becomes an unindexed path lookup, or a duplicate sample on
    #: every re-import of a non-A-F-I-R filename.
    #:
    #: Names shorten inside their group: `breseq_version` is `breseq["version"]`,
    #: `sequencing_date` is `sequencing["date"]`. The interop payload's keys do **not** move
    #: with them -- it has an external consumer -- so `_sample_info_list` translates.
    BRESEQ = "breseq"
    SEQUENCING = "sequencing"
    CURATION = "curation"

    @property
    def breseq(self):
        """What the breseq run reported: `version`, `reads`, `average_read_length`,
        `mean_coverage`, `percentage_mapped`.

        `breseq_summary.read_breseq_summary` builds four of these as a dict already, so the
        group is stored as it is built rather than fanned out a key at a time.
        """
        return self.record(self.BRESEQ)

    @property
    def sequencing(self):
        """How it was sequenced: `date`, `library_prep`, `reference_genome`."""
        return self.record(self.SEQUENCING)

    @property
    def curation(self):
        """What a person recorded: `medium_description`, `person`.

        The only group with a writer that is not an importer -- the sample edit page -- which
        is why `aledb_experiment.samples._MAX_LENGTHS` still refuses an over-long value. The
        column widths that used to back that check are gone; the check is now all there is.
        """
        return self.record(self.CURATION)

    @property
    def is_mixed(self):
        """The other half of `is_clonal`, spelled out.

        Every read site says either `is_clonal` or `is_mixed` and never `not is_clonal`, so
        reviewing the inversion that introduced them is a question about *words* -- does
        this line say the same one it used to mean? -- rather than about counting negations.
        `paths.clonal_filter()` and `paths.mixed_filter()` are the same idea for querysets.
        """
        return not self.is_clonal

    @property
    def experiment(self):
        return self.population.experiment

    @property
    def population_name(self):
        return self.population.name

    @property
    def label(self):
        """What this sample is called, everywhere one is named.

        The sample's own `description` if it has one, and otherwise its coordinate. That
        preference is why an import writes the filename into `description` for a name that
        says something (`Ara-2_500gen_763A`) and leaves it empty for one that only repeats
        the coordinate -- filling the second would relabel every column with a filename.

        It was `label`, which named the three levels it joined; two of them
        no longer exist and the third had moved. `aledb_experiment.coordinates` owns the
        format now, because this was one of two places writing it out by hand.
        """
        if self.description:
            return self.description
        return coordinates.format_coordinate(
            self.population_name, self.time_point, self.name)

    @property
    def qualified_label(self):
        """The label with its experiment in front, for the places that show samples from
        more than one -- the CSV export's column headings and the interop payload."""
        return self.experiment.name + " " + self.label


class UncalledRegion(models.Model):
    """A stretch of a sample's genome no call could be made over.

    Written from a GenomeDiff's `MC` (missing coverage) evidence records, which is why it
    was called `UncalledRegion` -- a name that described the file it came
    out of rather than the thing it stores, and led with "Unassigned", which stopped meaning
    anything when the columns it referred to went. What it holds is a region nothing is known
    about, however that came to be known.

    **It is not only a statistic.** aledb-phylogeny reads these to decide which cells of its
    character matrix are *ambiguous*: a mutation inside one of these regions is unknown for
    that sample, not absent. Without them `_encode` falls through to its next branch and
    scores the site ancestral -- a claim where there was an absence, which moves branches and
    changes the parsimony score with nothing raised. `aledb_stats` counts them per sample and
    `aledb_import.reference_rename` rewrites `seq_id` with everything else that stores one.

    `start` and `end` are **integers**. They were `CharField`s, so every reader had to cast
    before comparing -- and a reader that forgot compared as text, where `"1000"` sorts
    before `"9"` and a region silently covers the wrong positions. genomediff already parses
    them as ints; the column was the only thing making them strings.

    Eight further columns -- reads_left_url, reads_right_url, coverage, size, reads_left,
    reads_right, gene, description -- were scraped out of breseq's index.html and attached
    here. Nothing ever read any of them, and they went with the HTML report support.
    """

    seq_id = models.CharField(max_length=100)
    start = models.IntegerField()
    end = models.IntegerField()
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE)

    class Meta:
        verbose_name_plural = "uncalled regions"


class Mutation(SupplementalDataMixin):
    mutation_type = models.CharField(max_length=3,
                                     null=True,
                                     help_text="""Use breseq mutation codes, see the genome diff site
                                     on the barrick lab wiki (http://tinyurl.com/l3fvnap) for more
                                     information""")
    feature_length = models.IntegerField(blank=True,
                                         null=True)
    sequence_change = models.CharField(max_length=200)
    protein_change = models.CharField(max_length=300,
                                      default="")
    gene = models.CharField(max_length=19000, blank=True, null=True)  # TODO: use TextField for this.
    product = models.TextField(default="", null=True)
    #: The contig this mutation sits on -- breseq's own `seq_id`, and what every other
    #: spelling of it in the suite already said: the `.gd` attribute, `UncalledRegion.seq_id`,
    #: `AnnotatedSequence.seq_id`, the add form's field, the mutation table's column.
    #:
    #: It was `reseq_reference`, which read like the *genome* and shared its spelling with
    #: `Sample.reference_genome`, which genuinely is one. `to_gd_line` shows what the rename
    #: bought: `{'seq_id': self.seq_id}` where it used to have to translate.
    #:
    #: **It is one of the six `MUTATION_KEY_FIELDS`**, so it is part of a mutation's
    #: identity in the change log as well as in `get_or_create`. A `mutation_identity` blob
    #: written before the rename carries the old key and would resolve to None; the database
    #: is regenerated here, so there is nothing to migrate, but a deployment with a live log
    #: would need those blobs rewritten.
    seq_id = models.CharField(max_length=200, **blank_field)
    tags = models.CharField(max_length=500, **blank_field)

    # Mutations belong to one experiment. Two experiments that call the same
    # variant get their own rows, so re-annotating one against a new reference
    # cannot silently rewrite another's annotation.
    experiment = models.ForeignKey("aledb_experiment.Experiment",
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
    #: Where the mutation starts on `seq_id`, 1-based -- the `.gd` record's own `position`.
    #:
    #: **This was two columns.** `position` held the record's value and `start_position` held
    #: what `annotate.annotator.mutation_interval` computed, and for everything ALEdb stores
    #: those are the same number: that function returns `(position, ...)` for every type in
    #: `MUTATION_TYPES`, and the only types whose start differs -- the MC/UN/CN evidence
    #: entries -- are filtered out before annotation and are never stored as mutations.
    #:
    #: So one column, named for which end it is. `end_position` beside it is what actually
    #: carries the extent, and stays nullable because an unannotated mutation has none.
    #:
    #: **Written once, at creation, and never afterwards.** It is one of the six
    #: `MUTATION_KEY_FIELDS`, so it is part of a mutation's identity in `get_or_create` and in
    #: the change log -- an annotator that rewrote it would fork the row on the next import.
    #: That is why `annotation.POSITION_COLUMNS` no longer lists it.
    start_position = models.IntegerField()
    end_position = models.IntegerField(**blank_field)

    # Everything else breseq annotates -- gene_position, gene_strand, the codon_*
    # and aa_* fields, genes_overlapping/inactivated/promoter and their locus_tag
    # counterparts, transl_table. Nothing queries these; they are read whole, per
    # row, to render a mutation. Keeping them here means a new annotation field
    # needs no migration.
    #
    # Deliberately NOT folded into `supplemental_data`: this is derived display markup, it is
    # rewritten on every re-annotation, and it has a different lifecycle from a record that
    # arrives once with an import and never moves again.
    annotation = models.JSONField(**blank_field)

    #: The kind of record core writes here: the verbatim GenomeDiff the mutation was
    #: imported from.
    #:
    #: **The nesting is what enforces the verbatim rule.** This was `gd_data`, holding the
    #: record flat, and `to_gd_line()` splats every key it finds onto the line it emits for
    #: `gdtools APPLY` -- so anything a second writer put here landed in a `.gd` file. The
    #: rule was a comment, restated on `annotation`, on `MutationCall.evidence` and in
    #: `gd_import`, because a comment is all there was. `to_gd_line` reads one key now, and a
    #: foreign key in this column cannot reach an emitted line unless it targets this one.
    GENOME_DIFF = "genome_diff"

    @property
    def genome_diff(self):
        """The verbatim GenomeDiff record, or `{}` for a row that has none.

        Every reader goes through this rather than spelling two levels. `{}` rather than
        None because every guard on the old flat column was a truthiness check, and the two
        were already interchangeable everywhere.
        """
        return self.record(self.GENOME_DIFF)

    @classmethod
    def genome_diff_container(cls, record):
        """`supplemental_data` holding one GenomeDiff record and nothing else.

        For a caller building a Mutation from scratch -- the importer, the editor's record
        builder, a fixture. Spelling the two levels out at each of those is how the nesting
        would drift.
        """
        return {cls.COMPONENT: {cls.GENOME_DIFF: record}}

    def __unicode__(self):
        return u"%d %s" % (self.start_position,
                           self.sequence_change)

    def to_gd_line(self) -> str:
        """Reconstruct this mutation's GenomeDiff line (for gdtools APPLY).

        Prefers the verbatim record; falls back to a best-effort line built from the scalar
        columns for legacy rows that have none (that fallback is not guaranteed
        APPLY-complete — the discrete alleles were not captured for those rows).

        **It splats every key it is given, and that is why it reads one key rather than the
        column.** `supplemental_data` is shared — a plugin may keep its own import records
        beside this one — and every remaining key of whatever this reads lands on the emitted
        line. Reading `genome_diff` is what makes "nothing but the raw record" a property of
        the code instead of a comment three files repeat.
        """
        from genomediff.records import Record, TYPE_SPECIFIC_FIELDS

        record = self.genome_diff
        if record:
            data = dict(record)
            record_type = data.pop('type', self.mutation_type)
            record_id = data.pop('id', self.id)
            parent_ids = data.pop('parent_ids', None)
            return str(Record(record_type, record_id, parent_ids=parent_ids, **data))

        if self.mutation_type not in TYPE_SPECIFIC_FIELDS:
            return ""
        # The `.gd` field keeps breseq's name; only our column was ambiguous.
        attributes = {'seq_id': self.seq_id, 'position': self.start_position}
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
        name = self.seq_id or ''
        return name == self.ECOCYC_ACCESSION or name.startswith(self.ECOCYC_ACCESSION + '.')

    def ecocyc_gene_urls(self) -> str:
        """Gene links for a mutation table cell.

        A mutation spanning more than `GENE_LIST_LIMIT` genes renders its count instead of its
        names, the same answer `get_gene_table_entry` gives -- one link per gene over a
        4,318-gene inversion is a third of a megabyte in one cell, and no list that long is
        read. The import path stops recording the names at the same limit.
        """
        names = get_gene_list(self.gene)
        if len(names) > GENE_LIST_LIMIT:
            return mark_safe("%d genes" % len(names))
        return mark_safe(", ".join(get_ecocyc_gene_list(names, self.is_ecocyc_gene())))


class MutationCall(SupplementalDataMixin):
    """One caller's assertion about one mutation in one sample.

    Written by `gd_import` for every record in a sample's `.gd`, and by
    `aledb_mutation_editor` for a mutation somebody adds or copies by hand. It is the row the
    edit log works at, the row every cross-sample table has a cell for, and the row an edit
    hard-deletes -- never the `Mutation`, whose primary key is stored as a bare integer in
    aledb-phylogeny's `branch_mutations` and in every exported CSV.

    **It carries `supplemental_data` as well as `evidence`, and the two are not duplicates.**
    `evidence` is breseq's read counts for this call, in breseq's own shape, read by the
    mutation table to render a cell. `supplemental_data` is the namespaced container every
    other row has: the record this call *arrived* with, keyed by the component that wrote it.

    The VCF import is what needed it, and it needs it because VCF's fields divide exactly
    along the line these models already draw. `INFO`, `QUAL` and `FILTER` describe a site and
    could in principle live on the `Mutation`; `FORMAT` and each sample column describe one
    sample's call and could not. In practice the whole line is kept here, per call -- see
    `aledb_import/vcf_import.py` for why a shared `Mutation` is the wrong home for any of it.
    """

    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, null=True)
    # make sure not delete mutation if there are associated mutation calls
    mutation = models.ForeignKey(Mutation, on_delete=models.DO_NOTHING)
    # Whether the mutation is in this sample. True is an assertion that it is there,
    # False that it was looked for and found absent, null that nothing was recorded. It used
    # to sit beside `breseq_present` and `gatk_present`, one flag per caller, and every read
    # path asked those rather than this -- so a mutation a *person* added, which no caller
    # found, was absent from every cross-sample table. `source` says who asserted it; this
    # says whether it is there.
    present = models.BooleanField(null=True)

    # Whatever a caller said about *this sample's* call beyond the three columns above --
    # read counts, likelihoods, per-sample coverage. Four scalar columns stood here
    # (`wt_reads`, `mutated_reads`, `other_reads`, `reference_genome_likelihood`) and no
    # import path had ever written one of them.
    #
    # The same argument as `Mutation.annotation`, one level down: nothing queries these, they
    # are read whole and per row to render a cell, and a caller with a field we have no
    # column for should not need a migration. What differs is the *scope* -- `annotation` and
    # `supplemental_data` describe the mutation, which every sample carrying it shares, while this
    # describes one sample's evidence for it and is exactly what cannot live up there.
    #
    # Deliberately not folded into `supplemental_data`: that column is per-mutation and this
    # is per-call -- one sample's read counts attached to a row every sample shares.
    evidence = models.JSONField(**blank_field)

    # The one frequency. `frequency_gatk` sat beside it, for the GATK half of a gdtools
    # COMPARE merge that no import path has ever written -- 0 of 74,859 rows had a value.
    # Being always null was not merely useless: `aledb_filter` ANDed a `frequency_gatk__lt`
    # term into the exclusion, and a comparison against null is never true, so the frequency
    # cutoff excluded nothing at all.
    #
    # **A float, and it was `DecimalField(max_digits=5, decimal_places=4)`.** breseq's
    # polymorphism mode reports more precision than four places -- `frequency=8.39314286e-01`
    # is a real value out of the fixtures -- and the column rounded it to `0.8393`. The
    # precision was never lost from the *row*: the same import writes the full float into
    # `Mutation.supplemental_data`'s GenomeDiff record, so the copy being degraded was the
    # queryable one, which is backwards.
    #
    # Nothing has to *show* the extra digits, and nothing does. Every display site formats
    # explicitly -- `"%.2f"` in `mutation_table_builder`, `"%.1f%%"` in `breseq_report`,
    # `"%2f"` in the interop payload -- and no template renders the value raw. Nothing
    # aggregates or orders on it either; the only query use is `aledb_filter`'s two bounds.
    frequency = models.FloatField(null=True)
    # Which caller produced this call. Imports record "breseq"; other
    # callers can be added alongside. Left null on rows imported before this
    # existed, which came from a gdtools COMPARE merge of breseq and
    # GATK/CNVnator, so "breseq" would misrepresent them.
    source = models.CharField(max_length=50, db_index=True, blank=True, null=True)

    def get_experiment_id(self):
        return self.sample.population.experiment_id



class ReferenceSequences(models.Model):
    """The single reference genome shared by every sample in an experiment.

    breseq writes `data/reference.gff3` and `data/reference.fasta` alongside each run, so the
    reference arrives with the results rather than being uploaded separately. The first
    imported sample establishes it; every later sample must hash-match or be rejected, which
    is what keeps an experiment from silently ending up with mixed references.

    The files themselves live in the managed store, at a path derived from
    `experiment_id` -- see aledb_common.store, whose `experiment_reference_dir` keeps that
    name: it is a directory on disk, not this table.

    **Plural because one row holds many sequences**: `seq_ids` is the contig list, so this
    is a reference *genome* and not a single contig, while still being one row per
    experiment. It was `ExperimentReference`, which named the relation rather than the
    thing.

    `aledb_import.annotate.model.LoadedReferenceSequences` is the same genome *parsed*, held
    in memory for the annotator and the mutation editor's validator and built from the files
    this row points at. It briefly wore this exact name, which is why it now says `Loaded`:
    `aledb_mutation_editor.validation` handles both, and two classes a letter apart in one
    module is the shape of confusion this suite keeps having to undo.
    """

    experiment = models.OneToOneField("aledb_experiment.Experiment",
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

    def __str__(self):
        return "Reference for %s" % (self.experiment_id,)

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


class DatabaseSequenceLink(models.Model):
    """One reference contig, matched -- or not -- to a sequence database's record.

    The NCBI Sequence Viewer draws a locus from NCBI's own annotation, and it is addressed by
    accession rather than by a file. Nothing in this repo stores an accession: the importer
    takes a GenBank's LOCUS name (`NC_000913`) and not its VERSION (`NC_000913.3`), because
    breseq does, and every seq_id in a `.gd` is therefore unversioned. So a contig name is an
    accession only by convention -- and drawing a mutation in the coordinate space of the
    wrong genome is not a visible failure, it is a convincing page pointing at the wrong gene.

    Hence this table, and the rule it exists to enforce: **a contig is drawn in NCBI's
    coordinates only once NCBI has confirmed the record is byte-for-byte our sequence.** The
    name is never the evidence.

    Keyed on the sequence digest rather than on a contig name or an experiment, so that the
    verdict *cannot outlive the sequence it was about*: the key is the bases, and there is no
    path by which a stored accession survives onto a different sequence. It also means the
    same genome imported into ten experiments is verified once, and that a contig rename --
    which does not change the bases -- cannot invalidate it.

    `accession` is only ever a proposal until `status` is VERIFIED. Anyone with write access
    to any experiment carrying this sequence may propose one, which is a cross-experiment
    write and is deliberate: what decides the status is the database's sequence, not the
    proposer, so a wrong proposal can become a rejected verdict but never a wrong one.

    **`database` is why this is not called `NcbiSequence`, which it was.** Nothing above is
    NCBI's: a contig could be confirmed against ENA, DDBJ or an institutional archive and the
    digest key, the four failure states and "a name is never the evidence" would all read the
    same. What is NCBI's is the *protocol* -- eutils -- and the viewer, and those live in
    `aledb_sample.ncbi` and its templates rather than here.

    **It is a discriminator, not a plugin seam.** There is exactly one value and exactly one
    module that can speak to a database, and a second database means a sibling of `ncbi.py`
    rather than a branch inside it. What the column buys today is only that the *table* stops
    presuming -- adding the second one is then a row, not a migration that has to unpick a
    unique constraint on `sha256`.
    """

    #: The database this row's `accession` is an identifier in.
    NCBI_NUCLEOTIDE = "NCBI-nucleotide"

    DATABASE_CHOICES = [
        (NCBI_NUCLEOTIDE, "NCBI Nucleotide"),
    ]

    #: Nobody has said what this contig is. A missing row means this, so no backfill is ever
    #: needed -- the same convention `DerivedDataState` uses for staleness.
    UNCHECKED = "unchecked"
    #: NCBI's record for `accession` is byte-for-byte this sequence. The only drawable state.
    VERIFIED = "verified"
    #: There is such a record and it is a different sequence -- a sibling strain, or another
    #: assembly version. The interesting failure, and the one worth wording carefully.
    MISMATCH = "mismatch"
    #: NCBI has no record under that accession.
    NOT_FOUND = "not_found"
    #: The check could not be completed -- no network, a timeout, a malformed answer. Says
    #: nothing about whether the sequence matches, which is why it is not MISMATCH.
    ERROR = "error"

    STATUS_CHOICES = [
        (UNCHECKED, "Not checked"),
        (VERIFIED, "Verified against the database"),
        (MISMATCH, "Sequence does not match"),
        (NOT_FOUND, "No such record"),
        (ERROR, "Check failed"),
    ]

    #: Which database `accession` names. **Capitalised, where `status` above is not**, and
    #: that is a choice rather than an oversight: `status` is an internal vocabulary that only
    #: this code reads, while this names a thing in the world and is how people spell it.
    database = models.CharField(max_length=32, choices=DATABASE_CHOICES,
                                default=NCBI_NUCLEOTIDE)
    #: aledb_import.reference.sequence_digest() of this contig -- sha256 of its uppercased
    #: bases alone. Equal to the `sha256` of an ReferenceSequences.seq_ids entry, which is
    #: how a contig finds its row.
    #:
    #: **Unique with `database`, not on its own**, which it was. One digest may be recorded in
    #: several databases -- that is the whole point of the column beside it -- and a bare
    #: unique here would have let exactly one of them ever hold a row per sequence.
    sha256 = models.CharField(max_length=64)
    length = models.BigIntegerField()
    #: Versioned once verified: NCBI's own `accessionversion`, not whatever was typed. An
    #: unversioned proposal that verifies is stored as the version that actually matched.
    accession = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=UNCHECKED)
    #: Why it failed, in words, for a page and for `./aledb ncbi_accessions --list`. A status
    #: alone cannot distinguish "4,641,652 bases here, 4,558,660 there" from "same length,
    #: different bases", and those call for different next steps.
    detail = models.TextField(blank=True, default="")
    checked_at = models.DateTimeField(**blank_field)
    proposed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, **blank_field)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["database", "sha256"],
                                    name="unique_sequence_per_database"),
        ]
        verbose_name = "database sequence link"
        verbose_name_plural = "database sequence links"

    def __str__(self):
        return "%s %s (%s)" % (self.database,
                               self.accession or self.sha256[:12], self.status)

    @property
    def is_verified(self):
        return self.status == self.VERIFIED

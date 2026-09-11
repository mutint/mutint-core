"""What a sample was made from, and how to record it.

A sample is produced from something -- read files, an SRA accession, a `.gd` somebody exported,
a VCF -- and until now core kept none of it. The nearest thing was `Sample.source_name`, which
its own comment is careful to call *identity rather than information*: one string, the basename
a re-import matches on. `mutint-breseq` held the read filenames on its own `BreseqRun` row,
which is a plugin's, is deletable, and is not reachable from the sample.

**Stored in `Sample.supplemental_data` rather than a table**, as a fourth core group beside
`breseq`, `sequencing` and `curation`. It meets that column's stated test -- *"arrives with an
import, shares the row's lifetime, and is read whole rather than queried"* -- and the cost is
stated here once: asking *which samples came from SRR37077254* is a scan rather than an index.
Nothing does. If something ever must, `record_inputs` is already the one writer and moving the
storage under it is a contained change.

**This is a facility to call, not a tenth registry.** There is nothing for an app to
contribute and nobody would ever enumerate the writers -- the rule `mutint_import.staging`
states: *a registry whose entries nobody enumerates is a dictionary with ceremony.* A plugin
imports `record_inputs` and calls it, which is what `mutint-breseq` already does with the
`is_clonal` it knows better than the importer did.
"""

from collections import namedtuple

from mutint_sample.models import Sample

#: One thing a sample was made from. `group` ties mates together -- both files of a pair share
#: one -- and `mate` is 1, 2, or None for anything that is not half of a pair.
#:
#: An SRA accession is **one** entry in a group of its own even though it yields two files,
#: because the accession is what was given. What it expands to is the downloader's business.
Input = namedtuple("Input", ["kind", "value", "group", "mate"])
Input.__new__.__defaults__ = (0, None)

#: What sort of thing an entry's `value` names.
#:
#: **A discriminator, not a plugin seam**, exactly as `DatabaseSequenceLink.database` is: a new
#: kind is a constant here, not a registration point, because nothing enumerates them and the
#: display needs a label per kind that has to live somewhere anyway. `mutint-breseq` needs none
#: of its own -- it writes `KIND_READS`.
KIND_READS = "reads"
KIND_SRA = "sra"
KIND_GENOMEDIFF = "genomediff"
KIND_VCF = "vcf"
KIND_FOLDER = "folder"

#: How each kind is named in front of a person. Singular: a box lists entries, and every label
#: here is the name of one of them.
KIND_LABELS = {
    KIND_READS: "Read file",
    KIND_SRA: "SRA run",
    KIND_GENOMEDIFF: "GenomeDiff file",
    KIND_VCF: "VCF file",
    KIND_FOLDER: "breseq folder",
}

#: Where a value can be looked up, for the kinds that name something in the world. Only SRA
#: does today; it is here now so the box already links an accession the day the downloader
#: lands, rather than being the thing somebody remembers afterwards.
KIND_URLS = {
    KIND_SRA: "https://www.ncbi.nlm.nih.gov/sra/{value}",
}


def label_for(kind):
    """What to call this kind, falling back to the raw value rather than to nothing."""
    return KIND_LABELS.get(kind, kind)


def url_for(entry):
    """Where this entry's value can be looked up, or None."""
    template = KIND_URLS.get(entry.get("kind"))
    return template.format(value=entry.get("value", "")) if template else None


def record_inputs(sample, entries, replace=True, save=True):
    """Record what `sample` was made from.

    `save=False` sets the value without writing, for a caller already batching one
    `save(update_fields=[..., "supplemental_data"])` -- the shape `breseq_folder` uses for the
    `BRESEQ` group. It is `set_record`'s own keyword, passed straight through.

    `entries` are `Input` tuples. **`replace=True`** because a re-import supersedes a sample
    rather than adding to it -- `_database_gd_mutations` clears its calls before writing the
    new ones, and its inputs should go the same way rather than accumulating an entry per
    re-run. `set_record` replaces the whole group in one write, which is exactly that.

    **Always called after the sample exists, never as a `get_or_create(defaults=...)` key.**
    `defaults` does not run when a re-import finds the sample already there, which is the gap
    that left `is_clonal` wrong on every re-run and that `mutint_breseq` has its own workaround
    for. The two existing post-creation writes -- `breseq_folder`'s `BRESEQ` group and
    `vcf_import`'s `VCF_RECORD` -- are the shape this follows.

    **Written under `mutint_core`**, not under the calling component's name, which bends the
    plugin guidance in `docs/plugin/data-model.md`. This is a core-defined record that a plugin
    contributes facts to rather than a plugin's own material: one shape, one reader, against a
    box that would otherwise have to walk every component's key and merge what it found.

    A caller passing nothing with `replace=True` clears the record, which is what re-importing
    a sample from something that says nothing about its inputs should do.
    """
    items = [{"kind": entry.kind, "value": entry.value, "group": entry.group,
              "mate": entry.mate}
             for entry in entries]
    if not replace:
        items = list(sample.inputs) + items
    sample.set_record(Sample.COMPONENT, Sample.INPUTS, {"items": items}, save=save)
    return items


def read_entries(names, group_start=1):
    """`Input`s for read files whose pairing is not known -- one group each.

    What core's own importers can say. breseq's rule for which files are mates lives in
    `mutint-breseq` (`pairing.read_file_sets`), so a manually uploaded folder records its read
    files **ungrouped** while a run launched through the plugin records the pairing it actually
    used. The asymmetry is real and is better than core guessing at a rule it does not hold.
    """
    return [Input(KIND_READS, name, group_start + offset, None)
            for offset, name in enumerate(names)]

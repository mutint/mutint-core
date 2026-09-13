# Importing VCF

Drop `.vcf` files on an experiment's **Import data** page, on the *Variant Call Format* tab. Each
one becomes samples with mutations, indistinguishable from samples imported from breseq.

The experiment needs a **reference genome** first. A VCF does not carry one, and the reference
is also what checks that the calls were made against the genome this experiment uses.

## What one file becomes

- A VCF with **sample columns** becomes one MutInt sample per column, named from the column
  headers. They share one mutation per site, so a variant called in nine of twelve samples is
  one row with nine filled cells.
- A **sites-only** VCF — no `FORMAT`, no sample columns — is one sample, named from the
  filename, exactly as a bare `.gd` would be.

Either way the name decides where the sample sits: `Ara-2_500gen_763A` places it on ALE
`Ara-2` at time point 500, `3-30000-1-1` names the coordinate directly, and anything else is
auto-numbered onto ALE 1. That is the same rule a `.gd` filename goes through.

A VCF can also say where its samples sit: `##sample=`, `##population=` and `##generation=`
header lines apply to every sample column, and `##SAMPLE=<ID=col,population=...,generation=...>`
places one column. A `metadata.csv` dropped alongside outranks both; see *Loading data*.

## Mutations share rows with breseq's

A VCF is converted to GenomeDiff on the way in, which is MutInt's own format. That is not a
formality: it means the same variant called by breseq and by GATK is **one mutation**, with a
call from each sample that has it, rather than two rows that happen to sit at the same
position.

VCF spells indels with an anchor base and GenomeDiff does not, so the conversion normalizes
first — trimming the shared bases and left-aligning — before deciding what the variant is.
Four things it can produce:

| the call | becomes |
|---|---|
| one base for one base | `SNP` |
| bases inserted | `INS` |
| bases removed | `DEL` |
| anything else | `SUB`, replacing *n* reference bases with the new sequence |

## What it will not import, and will tell you about

Each of these is reported against the line it came from; the rest of the file still imports.

- **A `REF` that disagrees with the reference genome.** Almost always a VCF called against a
  different assembly, or an off-by-one. This is the check worth having: without it the file
  imports perfectly and every position is wrong.
- **Symbolic alleles and breakends** — `<DEL>`, `<DUP>`, `A[chr:pos[`. Real VCF, but reading
  them means trusting a caller-specific convention, and a wrong structural variant is worse
  than an honest refusal.
- **Anything that changes nothing**, where REF and ALT are the same.

Genotypes are read as calls: `0`, `0/0` and `./.` are not mutations and produce nothing. A
multi-allelic line is split, and each sample's genotype picks its own allele — so two samples
calling different alleles at one site get two different mutations, which is what they are.

Frequency comes from the sample's own `AF`, then its `AD`, then the site's `AF`. A call with
none of those is taken as a full-frequency call, which is the right reading for a haploid
genome.

## Getting the VCF back out

`/import/vcf/<sample id>/export` returns the sample as the VCF it came from — the same header
and the same data lines, byte for byte for a single-sample file, plus one header line MutInt
adds: a `##SAMPLE=<ID=...>` line naming the sample's population, time point, name and whether
it is a clone or a population sample, as they are now. That line is what the importer reads
first when the file is dropped again, so a re-import lands where the sample sits rather than
where its filename would put it.

A sample that came from breseq rather than a VCF gets one **generated** from its mutations,
under a header MutInt writes: the reference's contigs, the placement line, and a `##source`
line naming MutInt. Its `QUAL` and `FILTER` are `.` and `INFO` carries the frequency as
`AF=`. It is a view for tools that speak VCF, and a lossy one: a mobile element insertion,
an amplification, an inversion or a conversion has no VCF spelling and is left out, and the
`##mutint_omitted=` header counts them by type. The sample's `.gd` is the complete record,
and is what to drop back on MutInt: re-importing a generated VCF over the sample replaces
its calls with only the ones the file could spell.

Mutations **added or edited in MutInt** since the import have no original line, so they are
rebuilt from the mutation, and the file says so in an `##mutint_regenerated=` header. Their
`QUAL`, `FILTER` and `INFO` are `.` — MutInt never had those values to keep.

## Mobile elements

Off unless a deployment turns it on (the `MUTINT_VCF_INFER_MOB` setting, in its settings module rather than the environment). When on, an insertion whose
sequence is exactly one of the reference's annotated repeat families becomes a `MOB` with that
family and a target-site duplication measured from the surrounding sequence. Anything
ambiguous — two families matching, a partial match, a reference with no annotated repeats —
stays an insertion, which is the honest answer.

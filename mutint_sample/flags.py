"""The flags a sample can carry: hypermutator, contaminated, low coverage.

Three booleans on `Sample`, described once here so the edit pages, the badges and the mutation
matrix agree about what each is called and how it is drawn. They replaced `Sample.tags`, a
comma-joined text column with a three-word vocabulary that three pages wrote and nothing read;
a flag is a fact about the dataset -- like the designated ancestor -- rather than a note to
whoever is looking, and a boolean is a fact a query can ask about.

**Nothing consumes them yet.** Convergence could leave a hypermutator out, phylogeny could drop
a contaminated sample; each of those is its own change with its own reason. Today a flag is
shown wherever a sample is named and edited where a sample is edited, and that is all.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    field: str       # the Sample column
    key: str         # the form field and the CSS suffix
    label: str       # what the checkbox and the badge say
    help: str        # what the edit page says under the checkbox


FLAGS = (
    Flag("is_hypermutator", "hypermutator", "Hypermutator",
         "This sample acquired mutations at a much higher rate than its lineage -- a mutator "
         "allele, usually -- so its mutation count is not comparable with the others'."),
    Flag("is_contaminated", "contaminated", "Contaminated",
         "The reads are not, or not only, this sample: a cross-contamination or a mix-up, so "
         "its calls should not be trusted."),
    Flag("is_low_coverage", "low_coverage", "Low coverage",
         "Too little sequence to call mutations reliably -- a failed library, a truncated run, or "
         "coverage that never reached what a confident call needs."),
)

FLAG_FIELDS = tuple(flag.field for flag in FLAGS)


def flags_of(sample):
    """The flags set on `sample`, in the order above."""
    return [flag for flag in FLAGS if getattr(sample, flag.field, False)]

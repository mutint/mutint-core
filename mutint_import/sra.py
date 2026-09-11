"""What an SRA accession means, once ENA has answered: runs, their files, and the samples they
make.

Pure: no network, no Django, no models. `mutint_import.sra_fetch` is what asks ENA and what
downloads; this module is the rules it applies to the answer, testable without either. It
sits beside `accessions.py` the way `ncbi_fetch` sits beside `mutint_sample.ncbi`: that module
parses the box, this one knows what the tokens are.

**The Sequence Read Archive is one archive under three names.** NCBI's SRA, EBI's ENA and
DDBJ mirror each other, so an accession is the same accession whichever prefix it wears --
`SRR`, `ERR` and `DRR` are all runs. The reads are fetched from ENA because ENA is the mirror
that serves them as plain `fastq.gz` over HTTPS with a checksum beside each file, which means
no tool has to be installed to read them; the *accession* is still the SRA's, and it is what a
sample records as its input. See `sra_fetch` for the request.

**Four kinds, and a shape check is safe here where `accessions.kind()` says it is not.** A
reference accession has no reliable shape, so that module checks characters only. An SRA
accession is a fixed prefix and digits, in a namespace NCBI controls, and a token that fits
none of them is not going to be found under any of them either -- so refusing it by shape
costs nothing a lookup would have recovered, and buys a sentence naming the four kinds
instead of ENA's empty answer.

**A study or a sample can be several runs, and which of them are one sample is decided
here.** A *run* is one sequencing of one library; a *sample* accession (a BioSample) is one
biological sample however many times it was sequenced; an *experiment* is one library. Every
run under a sample or experiment accession is therefore one MutInt sample with several read
files -- the same shape as a lane split dropped by hand. A study is one sample per BioSample
in it. `samples_in` is that rule and `sample_name_for` is what the sample is then called:
ENA's `sample_alias`, the name the submitter gave the BioSample, which is `REL768A` for the
LTEE's clones and a coordinate somebody typed for anything submitted from MutInt's world --
and the accession when the alias is blank or is not a name the consumer can use. The
*usable* test is the consumer's to pass in, because what counts as a sample name is the
consumer's rule (mutint-breseq's `SAMPLE_NAME_RE`), and this module has none.

**`library_layout` is carried for display and decides nothing.** Which files are mates is
breseq's filename rule (`mutint_breseq.pairing`), applied to the names ENA gives the files,
which are `<run>_1.fastq.gz` and `<run>_2.fastq.gz` for a pair and `<run>.fastq.gz` for
single reads -- and which already pair under that rule. A third file, `<run>.fastq.gz` beside
a pair, is ENA's orphan-reads file and is handed over like any other.
"""

import os
import re
from collections import OrderedDict, namedtuple

from mutint_import.accessions import AccessionError

RUN = "run"
SAMPLE = "sample"
EXPERIMENT = "experiment"
STUDY = "study"

#: What each kind is called in a sentence a person reads.
KIND_LABELS = {
    RUN: "run",
    SAMPLE: "sample",
    EXPERIMENT: "experiment",
    STUDY: "study",
}

#: The accepted shapes, one per kind. `[SED]` is NCBI, EBI and DDBJ, which issue the same
#: kinds under their own first letter; the BioSample and BioProject spellings (`SAMN`,
#: `PRJNA`, ...) are the same records under the umbrella databases' names, and ENA resolves
#: either. Order matters only for the sentence that lists them.
_SHAPES = (
    (RUN, re.compile(r"^[SED]RR\d+$", re.IGNORECASE)),
    (EXPERIMENT, re.compile(r"^[SED]RX\d+$", re.IGNORECASE)),
    (SAMPLE, re.compile(r"^(?:[SED]RS\d+|SAM(?:N|EA|D)\d+)$", re.IGNORECASE)),
    (STUDY, re.compile(r"^(?:[SED]RP\d+|PRJ(?:NA|EB|DB)\d+)$", re.IGNORECASE)),
)

#: The example each kind is named by in the refusal, so the sentence teaches the shapes.
_EXAMPLES = "a run (SRR…), a sample (SRS… or SAMN…), an experiment (SRX…) or a study (SRP… or PRJNA…)"

#: Characters a read file's name may take from ENA. The name becomes a path under a directory
#: this code chose, and ENA's names are `<run>_1.fastq.gz`; anything else is refused rather
#: than sanitised, because a rewritten name would no longer be the one the mate rule sees.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

#: The scheme ENA's file report leaves off. Its `fastq_ftp` column is `host/path`, and the
#: same host serves the same path over HTTPS -- which is what is used, being what a deployment
#: with an ordinary outbound proxy can reach.
_SCHEME = "https://"


def kind(token):
    """Which kind of record `token` names: RUN, SAMPLE, EXPERIMENT or STUDY.

    Raises `AccessionError` for a token of no recognised shape, naming the four kinds.
    """
    for name, shape in _SHAPES:
        if shape.match(token or ""):
            return name
    raise AccessionError(
        "%s is not an SRA accession -- expected %s." % (token, _EXAMPLES))


def label(name):
    return KIND_LABELS.get(name, name)


class Run:
    """One run, as one row of ENA's read-run report.

    `files` is `[{"name", "url", "md5", "bytes"}, ...]` in the order ENA lists them, which is
    `_1` before `_2`. `sample_accession` is the BioSample (`SAMN…`), the identity a study is
    grouped by; `secondary_sample_accession` is the same record's `SRS…` name.
    """

    def __init__(self, accession, files, sample_accession="", secondary_sample_accession="",
                 experiment_accession="", study_accession="", alias="", title="",
                 layout=""):
        self.accession = accession
        self.files = [dict(entry) for entry in files]
        self.sample_accession = sample_accession
        self.secondary_sample_accession = secondary_sample_accession
        self.experiment_accession = experiment_accession
        self.study_accession = study_accession
        self.alias = alias
        self.title = title
        self.layout = layout

    def __repr__(self):
        return "Run(%r, %d files)" % (self.accession, len(self.files))

    @property
    def bytes(self):
        return sum(entry.get("bytes") or 0 for entry in self.files)

    @property
    def filenames(self):
        return [entry["name"] for entry in self.files]

    def as_dict(self):
        return {
            "accession": self.accession,
            "sample_accession": self.sample_accession,
            "secondary_sample_accession": self.secondary_sample_accession,
            "experiment_accession": self.experiment_accession,
            "study_accession": self.study_accession,
            "alias": self.alias,
            "title": self.title,
            "layout": self.layout,
            "files": [dict(entry) for entry in self.files],
        }

    @classmethod
    def from_dict(cls, payload):
        return cls(
            payload.get("accession") or "",
            payload.get("files") or [],
            sample_accession=payload.get("sample_accession") or "",
            secondary_sample_accession=payload.get("secondary_sample_accession") or "",
            experiment_accession=payload.get("experiment_accession") or "",
            study_accession=payload.get("study_accession") or "",
            alias=payload.get("alias") or "",
            title=payload.get("title") or "",
            layout=payload.get("layout") or "")


def run_from_row(row):
    """A `Run` from one row of ENA's read-run report, as `sra_fetch` requests it.

    ENA lists a run's files as three `;`-joined columns -- paths, checksums, sizes -- in one
    order, and they are zipped here. A row whose columns disagree in length is refused rather
    than guessed at: a file without its checksum could not be verified after download, and
    that is the whole reason the checksum is asked for. A row with no files at all is a `Run`
    with `files == []`, which `sra_fetch.resolve` turns into the sentence; the row itself is a
    perfectly good record of a run ENA holds no FASTQ for.
    """
    accession = (row.get("run_accession") or "").strip()
    paths = _split(row.get("fastq_ftp"))
    md5s = _split(row.get("fastq_md5"))
    sizes = _split(row.get("fastq_bytes"))
    if not (len(paths) == len(md5s) == len(sizes)):
        raise AccessionError(
            "ENA listed %s's files without a checksum or size for each of them, so they "
            "could not be verified after download." % accession)

    files = []
    for path, md5, size in zip(paths, md5s, sizes):
        files.append({
            "name": safe_filename(path, accession),
            "url": path if "://" in path else _SCHEME + path,
            "md5": md5.strip().lower(),
            "bytes": _int(size),
        })
    return Run(
        accession, files,
        sample_accession=(row.get("sample_accession") or "").strip(),
        secondary_sample_accession=(row.get("secondary_sample_accession") or "").strip(),
        experiment_accession=(row.get("experiment_accession") or "").strip(),
        study_accession=(row.get("study_accession") or "").strip(),
        alias=(row.get("sample_alias") or "").strip(),
        title=(row.get("sample_title") or "").strip(),
        layout=(row.get("library_layout") or "").strip())


def safe_filename(path, accession=""):
    """The basename of an ENA file path, refused if it could not safely become a filename."""
    name = os.path.basename((path or "").strip().rstrip("/"))
    if not name or not _SAFE_NAME.match(name):
        raise AccessionError(
            "ENA named a file for %s that could not be stored as it is (%r)."
            % (accession or "this run", name))
    return name


def _split(value):
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class Plan:
    """One accession somebody typed, and the runs ENA says it is.

    Mirrors `ncbi_fetch.Plan`: the resolved answer rather than the raw text, so what is stored
    on a row between the request and the download is what was *found*, and the download does
    not get a second opinion about what was meant.
    """

    def __init__(self, typed, kind_name, runs):
        self.typed = typed
        self.kind = kind_name
        self.runs = list(runs)

    def __repr__(self):
        return "Plan(%r, %r, %d runs)" % (self.typed, self.kind, len(self.runs))

    @property
    def total_bytes(self):
        return sum(run.bytes for run in self.runs)

    @property
    def run_accessions(self):
        return [run.accession for run in self.runs]

    @property
    def filenames(self):
        return [name for run in self.runs for name in run.filenames]

    def restricted_to(self, run_accessions):
        """This plan over only the runs named, for the row that downloads those."""
        wanted = set(run_accessions)
        return Plan(self.typed, self.kind, [run for run in self.runs if run.accession in wanted])

    def as_dict(self):
        """The JSON-safe form a consumer stores on its own row until the download."""
        return {"typed": self.typed, "kind": self.kind,
                "runs": [run.as_dict() for run in self.runs]}

    @classmethod
    def from_dict(cls, payload):
        return cls(payload.get("typed") or "", payload.get("kind") or "",
                   [Run.from_dict(run) for run in payload.get("runs") or []])


def as_plans(plans):
    """`Plan`s from `Plan`s or their stored dicts, whichever a caller holds."""
    return [plan if isinstance(plan, Plan) else Plan.from_dict(plan) for plan in plans]


#: One MutInt sample an accession will become. `typed` and `kind` are the token it came from;
#: `biosample` is the `SAMN…` accession the runs share (blank when they do not); `runs` are
#: the runs whose files are this sample's reads.
SraSample = namedtuple("SraSample", ["typed", "kind", "biosample", "alias", "title", "runs"])


def samples_in(plans):
    """The samples `plans` make, in the order they would be launched.

    A run, sample or experiment accession is one sample holding every run under it. A study
    is one sample per BioSample in it, in order of first appearance -- what a study *is* is a
    set of samples, and a study whose runs all landed in one MutInt sample would be twenty
    clones' reads mapped as one. A study run with no BioSample at all is grouped under its
    own accession, so it is still one sample rather than lost.
    """
    samples = []
    for plan in as_plans(plans):
        if plan.kind != STUDY:
            first = plan.runs[0] if plan.runs else None
            samples.append(SraSample(
                plan.typed, plan.kind,
                first.sample_accession if first else "",
                first.alias if first else "",
                first.title if first else "",
                list(plan.runs)))
            continue
        grouped = OrderedDict()
        for run in plan.runs:
            grouped.setdefault(run.sample_accession or run.accession, []).append(run)
        for biosample, runs in grouped.items():
            samples.append(SraSample(
                plan.typed, plan.kind, biosample, runs[0].alias, runs[0].title, runs))
    return samples


def sample_name_for(sample, usable=lambda name: True):
    """What `sample` is called: the submitter's alias, or the accession it came from.

    `usable(name)` is the consumer's rule for what can be a sample name at all; an alias that
    fails it falls back rather than being rewritten, because a name somebody else typed and a
    name this code invented from it are two different claims. The fallback is the token typed
    for a run, sample or experiment, and the BioSample for a member of a study, since the
    study's own accession would name every one of its samples alike.
    """
    alias = (sample.alias or "").strip()
    if alias and usable(alias):
        return alias
    if sample.kind == STUDY and sample.biosample:
        return sample.biosample
    return sample.typed


def filenames_by_run(plans):
    """`{filename: run_accession}` over every file `plans` will download.

    Raises `AccessionError` if two runs would write one name -- which ENA does not do for two
    different runs, so it means one run reached twice, and `sra_fetch.resolve` refuses that
    earlier with a sentence naming both tokens. This is the check that holds if it did not.
    """
    names = {}
    for plan in as_plans(plans):
        for run in plan.runs:
            for name in run.filenames:
                if name in names and names[name] != run.accession:
                    raise AccessionError(
                        "%s would be downloaded twice, for %s and for %s."
                        % (name, names[name], run.accession))
                names[name] = run.accession
    return names

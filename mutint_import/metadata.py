"""Where a sample sits, said by a `metadata.csv` or by the file's own header.

A sample's coordinate -- population, time point, sample -- used to come from one place: its
filename, read by `sample_names.parse_sample_identity`. That rule stays, and two others rank
above it, asked in this order at the one seam (`gd_import.import_document_as_sample`):

1. **A `metadata.csv` dropped with the data.** Columns `sample,population,time_point,data`,
   with an optional `sample_type` (population/mixed, or clone/individual/isolate) between
   the last two (a header row, any column order); `#` lines and blank lines are ignored; `data` names the
   inputs the row places, several separated by `;`, and rows with the same coordinate may
   repeat to name more. An input is a breseq results folder's name, a `.gd` or VCF filename
   (the extension may be left off), or -- on the breseq launcher -- a read file's name or a
   stem shared by several. The file is read by `import_registry.run_import`, never claimed by
   a handler and never a unit of its own, and installed in a slot here for the seam to ask,
   exactly as `import_progress.reporting()` installs its reporter.
2. **The file's own header.** A `.gd`'s `#=KEY value` lines and a VCF's `##key=value` lines,
   under the synonyms in `SYNONYMS` (`sample`/`name`, `population`/`treatment`/`condition`,
   `time_point`/`generation`/`transfer`/`time`...), so a file can carry its own placement.
3. **The filename**, as before.

**Why the override happens at the seam and not as a relabel afterwards.** A filename that
itself parses would land on the filename's coordinate first and then be moved -- and the
next import of that file would find nothing at its coordinate and create a second sample.
Deciding before the row exists means a same-metadata re-import is a no-op, because the
chain is `get_or_create` on the coordinate. `source_name` stays the filename either way; it
is what re-import and mutint-breseq match on.

**A malformed CSV refuses the whole drop.** A missing column or a row that cannot be read
would otherwise land everything unplaced, which is the outcome the file exists to prevent.
A row that names nothing in the drop, or an input no row names, is a warning and the import
proceeds; an input claimed by two rows is an error for that input alone.
"""

import csv
import io
import os
import re
from collections import namedtuple
from contextlib import contextmanager

FILENAME = "metadata.csv"
COLUMNS = ("sample", "population", "time_point", "data")
#: May be present or not: `sample_type`, between time_point and data, saying whether the
#: sample is a population (mixed) or a clone (individual, isolate). Blank leaves the
#: importer's own rule -- breseq's `-p` in the `.gd`'s command line -- in force.
OPTIONAL_COLUMNS = ("sample_type",)
POPULATION_TYPES = ("population", "mixed")
CLONE_TYPES = ("clone", "individual", "isolate")
DATA_SEPARATOR = ";"

#: Header keys a file may place itself with, each mapped to the column it means. Matched
#: after lowercasing and dropping spaces and underscores, so `Time point`, `time_point` and
#: `TIMEPOINT` are one key.
SYNONYMS = {
    "sample": "sample", "name": "sample",
    "population": "population", "treatment": "population", "condition": "population",
    "timepoint": "time_point", "time": "time_point", "generation": "time_point",
    "generations": "time_point", "transfer": "time_point", "transfers": "time_point",
    "sampletype": "sample_type",
}

def is_placement_key(key):
    """Whether a header key is one that places a sample -- anything `SYNONYMS` knows.

    The `.gd` export asks, so that a replayed header cannot carry a coordinate the sample
    has since been moved away from; the export writes the current one itself.
    """
    return _normalize_key(key) in SYNONYMS


#: What placed a sample; recorded per import so the summary can say which rule applied.
BY_CSV = "metadata.csv"
BY_HEADER = "header"
BY_FILENAME = "filename"

#: Extensions a `data` cell may leave off, and a filename may carry, when they are compared.
_OPTIONAL_EXTENSIONS = (".vcf.gz", ".vcf", ".gd")


class MetadataError(ValueError):
    """The file cannot be read as sample metadata; the message names the line at fault."""


class MetadataConflict(MetadataError):
    """One input is named by two rows that disagree."""


#: One row of the CSV: the coordinate and the inputs it places. `time_point` is an int or
#: None; `population` is "" for an unplaced sample; `is_clonal` is True for a clone, False
#: for a population sample, None when the row said nothing; `line` is the CSV line number.
Row = namedtuple("Row", "line sample population time_point data is_clonal",
                 defaults=(None,))


def _normalize_key(key):
    return re.sub(r"[\s_]+", "", (key or "").strip().lower())


def _strip_extension(name):
    lowered = name.lower()
    for extension in _OPTIONAL_EXTENSIONS:
        if lowered.endswith(extension):
            return name[:-len(extension)]
    return name


def _clean_coordinate(sample, population, time_point, where):
    """The three parts checked with the sample editor's rules; `where` names the source."""
    from mutint_experiment.samples import SampleEditError, _label, optional_time_point

    try:
        sample = _label(sample, "sample", where)
        population = ("" if population is None else str(population)).strip()
        if population:
            population = _label(population, "population", where)
        time_point = optional_time_point(time_point, "time point", where)
    except SampleEditError as error:
        raise MetadataError(str(error))
    if bool(population) != (time_point is not None):
        raise MetadataError(
            "%s: population and time point go together -- give both, or neither for a "
            "sample that is not placed yet." % where)
    return sample, population, time_point


def _clean_sample_type(value, where):
    """`is_clonal` from a sample_type cell: True, False, or None for blank."""
    value = ("" if value is None else str(value)).strip().lower()
    if not value:
        return None
    if value in POPULATION_TYPES:
        return False
    if value in CLONE_TYPES:
        return True
    raise MetadataError(
        "%s: sample_type must be one of %s (a population) or %s (a clone), not %r."
        % (where, ", ".join(POPULATION_TYPES), ", ".join(CLONE_TYPES), value))


class Metadata:
    """A parsed `metadata.csv`: its rows, and what they matched during one import."""

    def __init__(self, rows, source=FILENAME):
        self.rows = list(rows)
        self.source = source
        self._matched = {}      # row line -> [input names it placed]
        self._claimed = {}      # input name -> row line
        self._notes = []

    # --- matching -------------------------------------------------------------------------

    def _candidates(self, name):
        bare = _strip_extension(name)
        return {name, bare}

    def lookup(self, name):
        """The row naming `name` exactly, extension optional; None when there is none.

        Raises `MetadataConflict` when two rows name it: there is no telling which was meant,
        and importing under either would be a guess dressed as a placement.
        """
        wanted = self._candidates(name)
        hits = [row for row in self.rows
                if any(_strip_extension(token) in wanted or token in wanted
                       for token in row.data)]
        return self._settle(name, hits)

    def lookup_stem(self, filenames):
        """The row whose `data` names any of `filenames`, exactly or as a substring.

        For read files, where a cell such as `s1` should reach `s1_R1.fastq.gz` and its mate.
        The longest matching token wins, so `S12` beats `S1` for `S12_R1.fastq`; two rows
        matching one file is a conflict.
        """
        best = {}
        for row in self.rows:
            for token in row.data:
                if not token:
                    continue
                for filename in filenames:
                    if token == filename or token in filename:
                        if row.line not in best or len(token) > best[row.line]:
                            best[row.line] = len(token)
        if not best:
            return None
        longest = max(best.values())
        hits = [row for row in self.rows if best.get(row.line) == longest]
        return self._settle(" / ".join(sorted(filenames)) if len(filenames) > 1
                            else next(iter(filenames)), hits)

    def _settle(self, name, hits):
        if not hits:
            return None
        if len(hits) > 1:
            raise MetadataConflict(
                "%s is named by rows %s of %s; name it once."
                % (name, " and ".join(str(row.line) for row in hits), self.source))
        row = hits[0]
        self._matched.setdefault(row.line, []).append(name)
        self._claimed[name] = row.line
        return row

    # --- reporting ------------------------------------------------------------------------

    def note(self, message):
        self._notes.append(message)

    def report(self):
        """What this file did during the import it was installed for."""
        applied = sorted(name for names in self._matched.values() for name in names)
        unmatched = ["line %d (%s)" % (row.line, ", ".join(row.data))
                     for row in self.rows if row.line not in self._matched]
        return {"file": self.source, "rows": len(self.rows), "applied": applied,
                "unmatched_rows": unmatched, "warnings": list(self._notes)}


# --- parsing ------------------------------------------------------------------------------

def parse(text, source=FILENAME):
    """Read the CSV text into a `Metadata`, or raise `MetadataError` naming the line."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", "replace")
    else:
        text = text.lstrip("﻿")

    kept = []          # (line number, text) for the reader
    for number, line in enumerate(io.StringIO(text), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append((number, line.rstrip("\r\n")))
    if not kept:
        raise MetadataError("%s has no header row." % source)

    header_number, header_line = kept[0]
    reader = csv.reader([header_line])
    header = [_normalize_key(cell) for cell in next(reader)]
    mapped = []
    for cell in header:
        mapped.append(SYNONYMS.get(cell, cell))
    missing = [column for column in COLUMNS if column not in mapped]
    if missing:
        raise MetadataError(
            "%s line %d: the header lacks %s (it needs %s)."
            % (source, header_number, ", ".join(missing), ", ".join(COLUMNS)))
    index = {column: mapped.index(column) for column in COLUMNS}
    for column in OPTIONAL_COLUMNS:
        if column in mapped:
            index[column] = mapped.index(column)

    rows = {}      # coordinate -> Row, so repeated rows merge their data
    order = []
    for number, line in kept[1:]:
        cells = next(csv.reader([line]))
        if not any(cell.strip() for cell in cells):
            continue
        def cell(column):
            position = index.get(column)
            if position is None:
                return ""
            return cells[position].strip() if position < len(cells) else ""
        where = "%s line %d" % (source, number)
        sample, population, time_point = _clean_coordinate(
            cell("sample"), cell("population"), cell("time_point"), where)
        is_clonal = _clean_sample_type(cell("sample_type"), where)
        data = [token.strip() for token in cell("data").split(DATA_SEPARATOR)
                if token.strip()]
        if not data:
            raise MetadataError("%s: the data column names no input." % where)
        key = (sample, population, time_point)
        if key in rows:
            existing = rows[key]
            if existing.is_clonal is not None and is_clonal is not None \
                    and existing.is_clonal != is_clonal:
                raise MetadataError(
                    "%s: the same sample is a clone on one row and a population on another."
                    % where)
            rows[key] = existing._replace(
                data=existing.data + data,
                is_clonal=existing.is_clonal if is_clonal is None else is_clonal)
        else:
            rows[key] = Row(number, sample, population, time_point, data, is_clonal)
            order.append(key)
    return Metadata([rows[key] for key in order], source=source)


def coordinate_from_headers(mapping, where="the file's header", filename_identity=None,
                            filename_stem=None):
    """A `Row` from a file's own header fields, or None when they say nothing.

    `mapping` is whatever the document exposes -- a `.gd`'s `MetadataDict`, or the dict the
    VCF import builds from its `##` lines. Keys are matched through `SYNONYMS`, an exact
    column name winning over a synonym (`#=POPULATION` over `#=TREATMENT` when a file carries
    both, as the LTEE's do). `#=SAMPLE_TYPE population` says the sample is mixed, the way
    the CSV column does.

    **Each field the header names overrides the filename's; each it leaves out comes from
    there.** `filename_identity` is what `parse_sample_identity` read out of the name, or
    None; `filename_stem` is the name itself. So a `.gd` carrying `#=POPULATION Ara-3` and
    `#=TIME 30000` and named `3-30000-1-1.gd` lands at Ara-3 / 30000 / 1-1, and one carrying
    only `#=SAMPLE clone7` under an unparseable filename is an unplaced sample called
    clone7. A header naming a population without a time point (or the reverse), with the
    filename supplying neither, is refused the way a CSV row is.
    """
    found = {}
    keys = list(mapping.keys()) if hasattr(mapping, "keys") else []
    normalized = {_normalize_key(key): key for key in keys}
    # Exact names first, then the synonyms, so a synonym never shadows the real key.
    ordered = [key for key in normalized
               if key in COLUMNS or key in ("timepoint", "sampletype")]
    ordered += [key for key in normalized if key not in ordered]
    for key in ordered:
        column = SYNONYMS.get(key)
        if column is None or column in found:
            continue
        value = mapping.get(normalized[key])
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else ""
        value = ("" if value is None else str(value)).strip()
        if value:
            found[column] = value
    if not found:
        return None

    if "sample" not in found:
        if filename_identity is not None:
            found["sample"] = sample_label_of(filename_identity)
        elif filename_stem:
            found["sample"] = _strip_extension(filename_stem)
        else:
            raise MetadataError("%s names a population or time point but no sample." % where)
    if "population" not in found and "time_point" not in found and filename_identity is not None:
        found["population"] = filename_identity.population
        found["time_point"] = ("" if filename_identity.time_point is None
                               else str(filename_identity.time_point))
    elif "population" not in found and filename_identity is not None:
        found["population"] = filename_identity.population
    elif "time_point" not in found and filename_identity is not None \
            and filename_identity.time_point is not None:
        found["time_point"] = str(filename_identity.time_point)

    sample, population, time_point = _clean_coordinate(
        found["sample"], found.get("population", ""), found.get("time_point", ""), where)
    return Row(0, sample, population, time_point, [],
               _clean_sample_type(found.get("sample_type", ""), where))


def sample_label_of(identity):
    """The sample label a parsed filename yields -- `1-1` for `3-30000-1-1`, `763A` for
    `Ara-2_500gen_763A` -- so a header that names no sample keeps the filename's."""
    from mutint_import.sample_names import sample_label

    return sample_label(identity.name, identity.replicate)


# --- the file in a drop -------------------------------------------------------------------

def is_metadata_file(path):
    return os.path.basename(path).lower() == FILENAME


def split_paths(paths):
    """`(metadata paths, the rest)` -- the CSV is never one of the drop's inputs.

    **Except inside a MutInt archive.** An exported experiment carries a `metadata.csv`
    beside its `mutint.json`, and that one belongs to the archive: its importer reads it
    itself, so taking it here would install it for the whole drop and then report every
    row as naming nothing. A CSV with a manifest as its sibling is left in place.
    """
    from mutint_import.archive import MANIFEST

    archives = {os.path.dirname(path) for path in paths
                if os.path.basename(path) == MANIFEST}
    mine = [path for path in paths
            if is_metadata_file(path) and os.path.dirname(path) not in archives]
    rest = [path for path in paths if path not in mine]
    return mine, rest


def read_file(staged_root, relative):
    with open(os.path.join(staged_root, relative), "rb") as handle:
        return parse(handle.read(), source=relative.replace(os.sep, "/"))


# --- the slot the seam asks ---------------------------------------------------------------

_current = None


@contextmanager
def applying(metadata):
    """Install `metadata` for the import running inside the block.

    A module-level slot rather than an argument threaded through every handler, for the
    reason `import_progress` does the same: the handlers' signature is a published contract
    plugins implement, and the one function that needs this is three calls below them.
    """
    global _current
    previous = _current
    _current = metadata
    try:
        yield metadata
    finally:
        _current = previous


def current():
    return _current


def row_for(name):
    """The installed CSV's row for `name`, or None with nothing installed."""
    if _current is None:
        return None
    return _current.lookup(name)

#!/usr/bin/env python3
"""Validate a directory of XPMD metadata CSVs against Json_schema.json.

Each CSV is a two-column key/value file; the schema describes the *transposed* form, one
object per file, so the columns are pivoted into a dict before validating.

This used to open with an unconditional `return True` -- a kill switch, committed with the
comment "comment out following line to disable validation", which left the whole body below
it dead. Everything still called it: `aledb_import.ale_experiment` gates CLI uploads on it,
so every metadata directory was accepted no matter what it contained. That is why the parser
blew up on missing keys instead of rejecting the file cleanly.
"""

import csv
import io
import json
import logging
import os
import sys

from jsonschema import Draft4Validator

logger = logging.getLogger(__name__)

# Resolved from this module, not the process working directory. Callers used to hardcode
# "metadata/xpmdvalidator/Json_schema.json", which was both CWD-relative and stale -- the app
# was renamed to aledb_metadata -- so it would have raised FileNotFoundError the moment the
# kill switch above came out.
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Json_schema.json")


def _transposed_rows(csv_path):
    """The two-column key/value CSV pivoted into the one-object-per-file shape the schema wants.

    Done in memory. This used to write a `transposedFile.csv` into the process working
    directory and delete it only after the whole run -- so it littered the repo root, and two
    concurrent uploads would read each other's file.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        transposed = zip(*csv.reader(handle))

    buffer = io.StringIO()
    csv.writer(buffer).writerows(transposed)
    buffer.seek(0)
    return list(csv.DictReader(buffer))


def validation_errors(csv_directory_path, schema_file_path):
    """[(filename, [message, ...]), ...] for every CSV that fails the schema."""
    with open(schema_file_path, "r", encoding="utf-8") as handle:
        schema = json.load(handle)

    validator = Draft4Validator(schema)
    failures = []

    for filename in sorted(os.listdir(csv_directory_path)):
        if not filename.lower().endswith(".csv"):
            continue
        path = os.path.join(csv_directory_path, filename)
        try:
            rows = _transposed_rows(path)
            errors = sorted(validator.iter_errors(rows), key=str)
        except Exception as exc:
            # An unreadable or malformed CSV is a validation failure, not a pass. The old
            # code caught this, printed, and left the verdict at True.
            logger.exception("could not read metadata file %s", path)
            failures.append((filename, ["could not be read: %s" % (exc,)]))
            continue

        if errors:
            failures.append((filename, [
                "%s: %s" % ("/".join(str(part) for part in error.path) or "(root)",
                            error.message)
                for error in errors]))

    return failures


def is_valid(csv_directory_path, schema_file_path):
    """True when every CSV in the directory satisfies the schema."""
    failures = validation_errors(csv_directory_path, schema_file_path)

    for filename, messages in failures:
        logger.error("metadata file %s failed validation:", filename)
        for message in messages:
            logger.error("  %s", message)

    return not failures


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Input CSVfile directory followed by JsonSchemaFile directory')
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if is_valid(sys.argv[1], sys.argv[2]):
        print("Metadata files validated.")
    else:
        sys.exit(1)

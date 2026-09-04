"""
Load a reference for annotation, whatever format it arrived in.

Annotation needs more of a reference than the store keeps. The stored canonical
form (`aledb_import.reference.normalize_reference`) is deliberately genes-only
and flattened -- it exists so two spellings of one genome hash the same -- which
loses the CDS/tRNA distinction, translation tables, pseudogene flags, spliced
locations and repeat regions that breseq annotates from. So annotation reads the
reference as supplied, and `reference_store` keeps that original file alongside
the normalized pair for re-annotation later.
"""

import os

from aledb_import.annotate import genbank, gff3

FORMAT_GENBANK = 'genbank'
FORMAT_GFF3 = 'gff3'

GENBANK_SUFFIXES = ('.gbk', '.gb', '.genbank', '.gbff')
GFF3_SUFFIXES = ('.gff', '.gff3')


class UnsupportedReferenceFormat(Exception):
    """The reference is not in a format annotation can read."""


def detect_format(path, original_name=None):
    """
    Which loader can read this file.

    Named after `aledb_import.reference.detect_format`, but deliberately narrower:
    a bare FASTA carries no features, so it is annotation-useless and rejected
    here rather than silently producing empty gene names.
    """
    name = (original_name or os.path.basename(path)).lower()
    if name.endswith(GENBANK_SUFFIXES):
        return FORMAT_GENBANK
    if name.endswith(GFF3_SUFFIXES):
        return FORMAT_GFF3

    if genbank.looks_like_genbank(path):
        return FORMAT_GENBANK
    if gff3.looks_like_gff3(path):
        return FORMAT_GFF3

    raise UnsupportedReferenceFormat(
        '%s is not a GenBank or GFF3 file; annotation needs gene features, which '
        'a bare FASTA does not carry' % (original_name or path))


def load_reference(*paths, **kwargs):
    """Parse reference files into a LoadedReferenceSequences for the annotator."""
    original_name = kwargs.pop('original_name', None)
    if kwargs:
        raise TypeError('unexpected keyword arguments: %s' % ', '.join(sorted(kwargs)))

    formats = {detect_format(path, original_name) for path in paths}
    if len(formats) > 1:
        raise UnsupportedReferenceFormat(
            'cannot mix reference formats in one load: %s' % ', '.join(sorted(formats)))

    if formats == {FORMAT_GENBANK}:
        return genbank.load_genbank(*paths)
    return gff3.load_gff3(*paths)

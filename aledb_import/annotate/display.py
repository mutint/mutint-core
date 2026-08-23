"""
breseq's display strings for a mutation.

A port of add_html_fields_to_mutation and html_formatted_mutation_annotation
(output.cpp:1998-2130, 4459-4746). These are the `html_mutation`,
`html_mutation_annotation`, `html_gene_name`, `html_gene_product`,
`html_position` and `html_seq_id` fields that `gdtools ANNOTATE --add-html-fields`
writes, and that the breseq report renders as its Mutation / Annotation / Gene /
Description columns.

ALEdb needs them twice over:

  * `Mutation.sequence_change` and `Mutation.protein_change` have always been
    the tag-stripped text of `html_mutation` and `html_mutation_annotation`
    (builder/upload.py:224-234). Generating the same HTML and stripping it the
    same way keeps those columns byte-identical to what is already stored.
  * The per-sample breseq-style table renders the HTML directly.
"""

from bs4 import BeautifulSoup

MAX_NUCLEOTIDES_TO_SHOW = 20  # settings.cpp:1411

INTERGENIC_SEPARATOR = '/'
GENE_LIST_SEPARATOR = ','
MULTIPLE_SEPARATOR = '|'
HTML_MULTIPLE_SEPARATOR = '<br>'
NO_GENE_NAME = '–'
GENE_RANGE_SEPARATOR = '–'
GENE_STRAND_REVERSE = '<'

MAX_GENES_BEFORE_SUMMARY = 15
CODING_SNP_TYPES = ('nonsynonymous', 'synonymous', 'nonsense')


def _get(mutation, key, default=''):
    value = mutation.get(key, default)
    return default if value is None else str(value)


def _exists(mutation, key):
    """breseq's entry_exists: the key is present and carries a value."""
    return key in mutation and str(mutation[key]) != ''


def _int(mutation, key, default=0):
    try:
        return int(_get(mutation, key, str(default)))
    except ValueError:
        return default


def commify(value):
    """output.cpp:118-135 -- thousands separators, on a string of digits."""
    text = str(value)
    if not text:
        return text
    reversed_text = text[::-1]
    out = []
    for index, char in enumerate(reversed_text):
        out.append(char)
        if (index + 1) % 3 == 0 and index + 1 != len(text):
            out.append(',')
    return ''.join(reversed(out))


def nonbreaking(text):
    """output.cpp:139-152 -- keep a cell from wrapping mid-value."""
    return (text.replace('–', '&#8211;')
                .replace('-', '&#8209;')
                .replace(' ', '&nbsp;'))


def htmlize(text):
    """output.cpp:169-181 -- nonbreaking() without the space substitution."""
    return text.replace('–', '&#8211;').replace('-', '&#8209;')


def _italic(text):
    return '<i>%s</i>' % text


def _font(attributes, text):
    return '<font %s>%s</font>' % (attributes, text)


def format_repeat_name(name):
    """output.cpp:2099-2106 -- italicise the number in IS150, ISAba1, etc."""
    if name[:2] == 'IS':
        return 'IS' + _italic(name[2:])
    return name


def _underline_mutated_base(codon, codon_position):
    """output.cpp:2111-2127 -- mark which base of the codon changed."""
    try:
        position = int(codon_position)
    except (TypeError, ValueError):
        return codon
    return ''.join(
        _font('class="mutation_in_codon"', base) if index + 1 == position else base
        for index, base in enumerate(codon))


def _split_field(mutation, key):
    return _get(mutation, key).split(MULTIPLE_SEPARATOR)


def html_mutation_annotation(mutation):
    """
    output.cpp:1998-2097 -- the Annotation column.

    A coding SNP renders as "S123L (TCG->TTG)"; anything else falls back to
    gene_position ("intergenic (-1/+1)", "coding (123/456 nt)", ...). A mutation
    overlapping several genes renders one entry per gene.
    """
    snp_types = _split_field(mutation, 'snp_type') if _exists(mutation, 'snp_type') else []
    is_coding_snp = any(snp_type in CODING_SNP_TYPES for snp_type in snp_types)

    if is_coding_snp:
        codon_ref = _split_field(mutation, 'codon_ref_seq')
        codon_new = _split_field(mutation, 'codon_new_seq')
        aa_ref = _split_field(mutation, 'aa_ref_seq')
        aa_new = _split_field(mutation, 'aa_new_seq')
        aa_position = _split_field(mutation, 'aa_position')
        codon_number = _split_field(mutation, 'codon_number')
        codon_position = _split_field(mutation, 'codon_position')
        gene_position = _split_field(mutation, 'gene_position')

        multiple_snps = (_split_field(mutation, 'multiple_polymorphic_SNPs_in_same_codon')
                         if _exists(mutation, 'multiple_polymorphic_SNPs_in_same_codon') else [])
        indeterminate = (_split_field(mutation, 'codon_position_is_indeterminate')
                         if _exists(mutation, 'codon_position_is_indeterminate') else [])

        parts = []
        for index, snp_type in enumerate(snp_types):
            if snp_type in CODING_SNP_TYPES:
                piece = _font('class="snp_type_%s"' % snp_type,
                              aa_ref[index] + aa_position[index] + aa_new[index])
                piece += '&nbsp;(%s&rarr;%s)&nbsp;' % (
                    _underline_mutated_base(codon_ref[index], codon_position[index]),
                    _underline_mutated_base(codon_new[index], codon_position[index]))
                if codon_number[index] == '1':
                    piece += '&dagger;'      # initiation codon
                if index < len(multiple_snps) and multiple_snps[index] == '1':
                    piece += '&Dagger;'      # another SNP shares this codon
                if index < len(indeterminate) and indeterminate[index] == '1':
                    piece += '&ordm;'        # reading frame not certain
                parts.append(piece)
            else:
                parts.append(nonbreaking(gene_position[index]))
        annotation = HTML_MULTIPLE_SEPARATOR.join(parts)
    elif _exists(mutation, 'gene_position'):
        annotation = nonbreaking(HTML_MULTIPLE_SEPARATOR.join(
            _split_field(mutation, 'gene_position')))
    else:
        annotation = ''

    if '?' in annotation:
        annotation = '<span style="white-space: nowrap">%s</span>' % annotation
    return annotation


def _collapsed_gene_list(count, joined):
    """A large deletion's gene list, behind a Show button -- output.cpp:4645-4657.

    breseq has two branches here and the port only had the second: with JavaScript
    it emits the count plus a hidden list and a button, and under --no-javascript it
    emits the count, a <br> and the whole list. Taking the fallback meant a deletion
    spanning hundreds of genes stretched the column until the rest of the table
    scrolled off the page.

    Two deliberate differences from breseq's markup, both to survive being embedded
    in someone else's page rather than standing alone in a generated report:

      - No element ids and no inline onclick. breseq numbers each block
        `gene_hide_<type>_<id>` and wires it to global hideTog()/showTog(); a
        delegated listener on the table needs neither, so nothing has to invent ids
        that stay unique across a page it does not own.
      - `breseq_gene_list`, not breseq's `hidden`: Bootstrap already defines
        `.hidden` with `!important`, which this cannot toggle back off.

    Underscores in those class names, not hyphens, and that is load-bearing. Every
    field here goes through htmlize() on the way out (breseq does the same, to stop
    a gene name like insB-14 wrapping mid-name), which rewrites "-" as a non-breaking
    hyphen -- so a hyphenated class name arrives as breseq&#8209;gene&#8209;list and
    matches no stylesheet. breseq's own generated classes are underscored for
    unrelated reasons; this port has to be.

    The <noscript> copy is breseq's, and is why the list is still readable with
    scripting off -- the same reason breseq keeps its --no-javascript branch.
    """
    return ('<b>%d genes</b> '
            '<noscript>%s</noscript>'
            '<span class="breseq_gene_list" hidden>%s</span>'
            '<button type="button" class="breseq_gene_toggle">Show</button>'
            % (count, joined, joined))


def _html_gene_fields(mutation):
    """output.cpp:4459-4580 -- the Gene and Description columns."""
    gene_name = _get(mutation, 'gene_name')
    gene_product = _get(mutation, 'gene_product')

    if INTERGENIC_SEPARATOR in gene_name:
        names = gene_name.split(INTERGENIC_SEPARATOR)
        strands = _get(mutation, 'gene_strand').split(INTERGENIC_SEPARATOR)
        if len(names) != 2 or len(strands) != 2:
            return htmlize(gene_name), htmlize(gene_product)

        arrows = ['&nbsp;&larr;' if strand == GENE_STRAND_REVERSE else '&nbsp;&rarr;'
                  for strand in strands]
        html_name = _italic(names[0])
        if names[0] != NO_GENE_NAME:
            html_name += arrows[0]
        html_name += '&nbsp;' + INTERGENIC_SEPARATOR
        if names[1] != NO_GENE_NAME:
            html_name += arrows[1]
        html_name += '&nbsp;' + _italic(names[1])
        html_product = gene_product

    elif GENE_RANGE_SEPARATOR in gene_name or (
            gene_name[:1] == '[' and gene_name[-1:] == ']'):
        # A gene range: the gene NAMES live in gene_product here, not products.
        names = [name if name == NO_GENE_NAME else _italic(name)
                 for name in gene_product.split(GENE_LIST_SEPARATOR)]
        if GENE_RANGE_SEPARATOR in gene_name:
            html_name = names[0] + GENE_RANGE_SEPARATOR + names[-1]
        else:
            html_name = names[0]

        joined = (GENE_LIST_SEPARATOR + ' ').join(names)
        if len(names) < MAX_GENES_BEFORE_SUMMARY:
            html_product = joined
        else:
            html_product = _collapsed_gene_list(len(names), joined)

    else:
        names = gene_name.split(MULTIPLE_SEPARATOR)
        strands = _get(mutation, 'gene_strand').split(MULTIPLE_SEPARATOR)
        rendered = []
        for index, name in enumerate(names):
            if name == NO_GENE_NAME:
                rendered.append(name)
                continue
            piece = _italic(name)
            if index < len(strands):
                piece += ('&nbsp;&larr;' if strands[index] == GENE_STRAND_REVERSE
                          else '&nbsp;&rarr;')
            rendered.append(piece)
        html_name = HTML_MULTIPLE_SEPARATOR.join(rendered)
        html_product = gene_product.replace(MULTIPLE_SEPARATOR, HTML_MULTIPLE_SEPARATOR)

    return htmlize(html_name), htmlize(html_product)


def _html_mutation_column(mutation):
    """
    output.cpp:4593-4735 -- the Mutation column.

    Returns (html_mutation, annotation_override). DEL, INV and AMP replace the
    gene-position annotation with something specific to the event.
    """
    mutation_type = _get(mutation, 'type')
    new_seq = _get(mutation, 'new_seq')

    if mutation_type == 'SNP':
        return _get(mutation, 'ref_seq') + '&rarr;' + new_seq, None

    if mutation_type == 'INS':
        if _exists(mutation, 'repeat_seq'):
            return '(%s)<sub>%s&rarr;%s</sub>' % (
                _get(mutation, 'repeat_seq'), _get(mutation, 'repeat_ref_copies'),
                _get(mutation, 'repeat_new_copies')), None
        if _exists(mutation, 'repeat_length'):
            return '(%s bp)<sub>%s&rarr;%s</sub>' % (
                _get(mutation, 'repeat_length'), _get(mutation, 'repeat_ref_copies'),
                _get(mutation, 'repeat_new_copies')), None
        if len(new_seq) <= MAX_NUCLEOTIDES_TO_SHOW:
            return '+' + new_seq, None
        return '+%d bp' % len(new_seq), None

    if mutation_type == 'DEL':
        if _exists(mutation, 'repeat_seq'):
            repeat_seq = _get(mutation, 'repeat_seq')
            body = ('(%d-bp)' % len(repeat_seq) if len(repeat_seq) > 12
                    else '(%s)' % repeat_seq)
            return '%s<sub>%s&rarr;%s</sub>' % (
                body, _get(mutation, 'repeat_ref_copies'),
                _get(mutation, 'repeat_new_copies')), None

        html = nonbreaking('&Delta;' + commify(_get(mutation, 'size')) + ' bp')
        annotation = ''
        if _exists(mutation, 'mediated'):
            annotation = format_repeat_name(_get(mutation, 'mediated')) + '-mediated'
        if _exists(mutation, 'between'):
            annotation = 'between ' + format_repeat_name(_get(mutation, 'between'))
        if not annotation:
            annotation = nonbreaking(_get(mutation, 'gene_position'))
        return html, nonbreaking(annotation)

    if mutation_type == 'SUB':
        size = _get(mutation, 'size')
        if len(new_seq) <= 4:
            return nonbreaking('%s bp&rarr;%s' % (size, new_seq)), None
        return nonbreaking('%s bp&rarr;%d bp' % (size, len(new_seq))), None

    if mutation_type in ('CON', 'INT'):
        return nonbreaking('%s bp&rarr;%s' % (
            _get(mutation, 'size'), _get(mutation, 'region'))), None

    if mutation_type == 'MOB':
        prefix = ''
        if _exists(mutation, 'ins_start'):
            prefix += '+%s ' % _get(mutation, 'ins_start')
        if _exists(mutation, 'del_start'):
            prefix += '&Delta;%s bp ' % _get(mutation, 'del_start')
        parts = ['%s:: ' % prefix] if prefix else []

        strand = _int(mutation, 'strand')
        strand_char = {-1: '–', 0: '?', 1: '+'}.get(strand, '')
        parts.append('%s (%s)' % (format_repeat_name(_get(mutation, 'repeat_name')),
                                  strand_char))

        duplication_size = _int(mutation, 'duplication_size')
        if _exists(mutation, 'indeterminate_duplication_size'):
            parts.append(' ? bp')
        elif duplication_size > 0:
            parts.append(' +%d bp' % duplication_size)
        elif duplication_size < 0:
            parts.append(' &Delta;%d bp' % abs(duplication_size))

        suffix = ''
        if _exists(mutation, 'del_end'):
            suffix += ' &Delta;%s bp' % _get(mutation, 'del_end')
        if _exists(mutation, 'ins_end'):
            suffix += ' +%s' % _get(mutation, 'ins_end')
        if suffix:
            parts.append(' ::' + suffix)

        return nonbreaking(''.join(parts)), None

    if mutation_type == 'INV':
        html = nonbreaking(commify(_get(mutation, 'size')) + ' bp inversion')
        if _exists(mutation, 'between'):
            annotation = 'between ' + format_repeat_name(_get(mutation, 'between'))
        else:
            annotation = nonbreaking(_get(mutation, 'gene_position'))
        return html, nonbreaking(annotation)

    if mutation_type == 'AMP':
        html = nonbreaking('%s bp x %s' % (commify(_get(mutation, 'size')),
                                           _get(mutation, 'new_copy_number')))
        annotation = ('duplication' if _int(mutation, 'new_copy_number') == 2
                      else 'amplification')
        return html, annotation

    return '', None


def add_html_fields(mutation, overwrite=True):
    """
    Write breseq's six html_* display fields into the mutation dict, in place.
    The mutation must already have been annotated.

    With overwrite=False only missing fields are filled in. That matters for a
    GD that was annotated by an older gdtools: those carry html_mutation but no
    `ref_seq` (breseq only started writing it in 2019), so regenerating the
    Mutation column from scratch would turn "C→A" into "→A".
    """
    if not overwrite and all(mutation.get(key) for key in (
            'html_seq_id', 'html_position', 'html_gene_name', 'html_gene_product',
            'html_mutation', 'html_mutation_annotation')):
        return mutation

    html_gene_name, html_gene_product = _html_gene_fields(mutation)
    annotation = html_mutation_annotation(mutation)
    html_mutation, annotation_override = _html_mutation_column(mutation)
    if annotation_override is not None:
        annotation = annotation_override

    if _get(mutation, 'type') == 'INV':
        # An inversion is drawn as one region turning around, not as a list.
        html_gene_name = html_gene_name.replace(HTML_MULTIPLE_SEPARATOR, ' &#8634; ')
        html_gene_product = html_gene_product.replace(HTML_MULTIPLE_SEPARATOR, ' &#8634; ')

    fields = {
        'html_seq_id': nonbreaking(_get(mutation, 'seq_id')),
        'html_position': commify(_get(mutation, 'position')),
        'html_gene_name': html_gene_name,
        'html_gene_product': html_gene_product,
        'html_mutation': html_mutation,
        'html_mutation_annotation': annotation,
    }
    for key, value in fields.items():
        if overwrite or not mutation.get(key):
            mutation[key] = value
    return mutation


def text_from_html(html):
    """
    Flatten one of the html_* fields to plain text.

    This is the transform builder/upload.py has always applied to breseq's
    html_mutation and html_mutation_annotation, so keeping it means
    Mutation.sequence_change and Mutation.protein_change do not change shape.
    """
    if not html:
        return ''
    return BeautifulSoup(html, 'lxml').text.replace('\xa0', ' ').strip()


def text_mutation(mutation):
    """The Mutation column as plain text, e.g. "A→C", "Δ776 bp", "+G"."""
    return text_from_html(_get(mutation, 'html_mutation'))


def text_mutation_annotation(mutation):
    """The Annotation column as plain text, e.g. "F239L (TTT→TTG)"."""
    return text_from_html(_get(mutation, 'html_mutation_annotation'))

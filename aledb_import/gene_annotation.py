from aledb_common.util import (GENE_LIST_LIMIT, GENE_LIST_SPLIT,
                               GENE_RANGE_ANNOTATION_DELIMITER)

INTERGENIC_SPLIT_CHAR = '/'
BRESEQ_GENE_RANGE_CHAR = '–'  # en-dash; encoding varies in breseq output


def get_annotated_gene_list(breseq_mutation_gene_annotation, breseq_gene_product_annotation=None):
    """Convert a breseq gene annotation string to a list of gene names.

    Handles three annotation forms:
    - intergenic:  "geneA/geneB"  → ["geneA", "geneB"]
    - range:       "geneA–geneB"  → split by gene product string
    - single gene: "geneA"        → ["geneA"]

    A range spanning more than `GENE_LIST_LIMIT` genes records the **range** rather than the
    names -- see that constant. `mokC–[fimA]` is what breseq's own Gene column says about such
    a mutation, and it is what this returns.

    **The two splits here are not a mistake.** Counting uses `GENE_LIST_SPLIT`, which accepts a
    comma with or without a space, because `annotate.annotator` joins the names with a bare
    comma. Producing keeps `GENE_RANGE_ANNOTATION_DELIMITER`, which on that same string yields
    one element and so returns it unchanged -- and it has to stay that way: the caller joins
    this list into `Mutation.gene`, which is one of the seven fields
    `Mutation.objects.get_or_create` keys on, so a tidier split here would rewrite the stored
    string for every range mutation and fork all of them on the next import. Only the rows over
    the limit change, and `aledb_seq.0012` moves the ones already stored.
    """
    mutation_gene_str = _get_str_from_annotation(breseq_mutation_gene_annotation)
    gene_product_str = _get_str_from_annotation(breseq_gene_product_annotation) if breseq_gene_product_annotation is not None else None

    if mutation_gene_str is not None:
        if INTERGENIC_SPLIT_CHAR in mutation_gene_str:
            return mutation_gene_str.split(INTERGENIC_SPLIT_CHAR)
        elif BRESEQ_GENE_RANGE_CHAR in mutation_gene_str:
            recorded = gene_product_str.split(GENE_RANGE_ANNOTATION_DELIMITER)
            if len(GENE_LIST_SPLIT.split(gene_product_str)) > GENE_LIST_LIMIT:
                return [mutation_gene_str]
            return recorded
        else:
            return [mutation_gene_str]
    return []


def _get_str_from_annotation(annotation):
    try:
        return annotation.decode("utf-8")
    except AttributeError:
        return str(annotation)

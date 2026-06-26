from aledb_common.util import GENE_RANGE_ANNOTATION_DELIMITER

INTERGENIC_SPLIT_CHAR = '/'
BRESEQ_GENE_RANGE_CHAR = '–'  # en-dash; encoding varies in breseq output


def get_annotated_gene_list(breseq_mutation_gene_annotation, breseq_gene_product_annotation=None):
    """Convert a breseq gene annotation string to a list of gene names.

    Handles three annotation forms:
    - intergenic:  "geneA/geneB"  → ["geneA", "geneB"]
    - range:       "geneA–geneB"  → split by gene product string
    - single gene: "geneA"        → ["geneA"]
    """
    mutation_gene_str = _get_str_from_annotation(breseq_mutation_gene_annotation)
    gene_product_str = _get_str_from_annotation(breseq_gene_product_annotation) if breseq_gene_product_annotation is not None else None

    if mutation_gene_str is not None:
        if INTERGENIC_SPLIT_CHAR in mutation_gene_str:
            return mutation_gene_str.split(INTERGENIC_SPLIT_CHAR)
        elif BRESEQ_GENE_RANGE_CHAR in mutation_gene_str:
            return gene_product_str.split(GENE_RANGE_ANNOTATION_DELIMITER)
        else:
            return [mutation_gene_str]
    return []


def _get_str_from_annotation(annotation):
    try:
        return annotation.decode("utf-8")
    except AttributeError:
        return str(annotation)

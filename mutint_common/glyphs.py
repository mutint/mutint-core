"""Which glyph a mutation is drawn as, and what mutint-recurrent's three shades mean.

The glyph is the mutation's *type*, read from the columns the annotator promotes rather than
from any display string: `mutation_type` (breseq's three letters), `snp_type` (the functional
class of a base substitution) and `mutation_category` (which already applies breseq's 50 bp
large/small cutoff).

Every name here is a `<symbol id="glyph-<name>">` in `templates/glyphs/sprite.html`, which a
page includes once and draws from with `<use href="#glyph-<name>">`; `GLYPHS` is the legend's
order and the test that every glyph has a symbol. The symbols carry no paint of their own, so
the page decides the fill. Two components draw them: mutint-recurrent's table and gene plots,
and mutint-circos's rings. The rule and the sprite were mutint-recurrent's until the second
user appeared; plugins do not import each other, so they live here.

`SHADES` is mutint-recurrent's: its three words say what a mutation does to a gene, which
that plugin decides per gene. They are kept beside the glyphs because the two legends are
drawn together there.
"""


from mutint_sample.functional_change import functional_change_bucket

CIRCLE = "circle"                 # synonymous base substitution
SQUARE = "square"                 # nonsynonymous base substitution
TOMBSTONE = "tombstone"           # nonsense base substitution
DIAMOND_DOWN = "diamond-down"     # intergenic base substitution
DIAMOND = "diamond"               # base substitution in a non-coding gene or a pseudogene
TRIANGLE = "triangle"             # small (<= 50 bp) deletion: narrows upward
TRIANGLE_DOWN = "triangle-down"   # small (<= 50 bp) insertion: widens upward
BOWTIE = "bowtie"                 # mobile element insertion
TRAPEZOID = "trapezoid"           # large (> 50 bp) deletion: narrows upward, as the triangle does
TRAPEZOID_DOWN = "trapezoid-down" # large (> 50 bp) insertion or amplification: widens upward
PARALLELOGRAM = "parallelogram"   # gene conversion or integration
BARBELL = "barbell"               # inversion: two triangles pointing outward from a bar

#: Every glyph with the words the legend shows beside it, in legend order.
GLYPHS = (
    (CIRCLE, "synonymous base substitution"),
    (SQUARE, "nonsynonymous base substitution"),
    (TOMBSTONE, "nonsense base substitution"),
    (DIAMOND_DOWN, "intergenic base substitution"),
    (DIAMOND, "base substitution in an RNA gene or pseudogene"),
    (TRIANGLE, "small deletion (≤ 50 bp)"),
    (TRIANGLE_DOWN, "small insertion (≤ 50 bp)"),
    (BOWTIE, "mobile element insertion"),
    (TRAPEZOID, "large deletion (> 50 bp)"),
    (TRAPEZOID_DOWN, "large insertion or amplification (> 50 bp)"),
    (PARALLELOGRAM, "gene conversion or integration"),
    (BARBELL, "inversion"),
)

#: The three shades, in legend order, with what each says about the gene.
SOLID = "solid"
OUTLINE = "outline"
GRAY = "gray"
SHADES = (
    (SOLID, "predicted to inactivate the gene"),
    (OUTLINE, "no inactivation predicted"),
    (GRAY, "in the promoter, or in the last 20% of the gene"),
)

_SNP_GLYPHS = {
    "synonymous": CIRCLE,
    "nonsynonymous": SQUARE,
    "nonsense": TOMBSTONE,
    "intergenic": DIAMOND_DOWN,
    "noncoding": DIAMOND,
    "pseudogene": DIAMOND,
}


def glyph_for(mutation_type, snp_type, mutation_category, size_change=0):
    """The glyph for one mutation.

    `size_change` is the net change in length and is read only for a SUB, which breseq
    classifies by its replaced span: a SUB that lengthens the genome is drawn as an
    insertion, one that shortens it as a deletion, and one that replaces like with like as a
    multi-base substitution (the square). A SNP with no functional class -- imported before
    the reference was there -- is drawn as a square too, there being nothing better to say.

    A deletion's shapes narrow upward at both sizes (triangle, trapezoid) and an
    insertion's widen upward (inverted triangle, inverted trapezoid), so size changes the
    shape's top and never its direction.
    """
    large = mutation_category in ("large_deletion", "large_insertion",
                                  "large_amplification", "large_substitution")
    if mutation_type == "SNP":
        return _SNP_GLYPHS.get(functional_change_bucket(snp_type), SQUARE)
    if mutation_type == "DEL":
        return TRAPEZOID if large else TRIANGLE
    if mutation_type in ("INS", "AMP"):
        return TRAPEZOID_DOWN if large else TRIANGLE_DOWN
    if mutation_type == "SUB":
        if size_change > 0:
            return TRAPEZOID_DOWN if large else TRIANGLE_DOWN
        if size_change < 0:
            return TRAPEZOID if large else TRIANGLE
        return SQUARE
    if mutation_type == "MOB":
        return BOWTIE
    if mutation_type in ("CON", "INT"):
        return PARALLELOGRAM
    if mutation_type == "INV":
        return BARBELL
    return SQUARE


def size_change(record):
    """The net change in length a GenomeDiff record makes: its `new_seq` less its `size`.

    `glyph_for` reads it for a SUB alone. `record` is `Mutation.genome_diff`; 0 when the record
    is missing or unreadable, which draws the SUB as the square.
    """
    try:
        return len(str(record.get("new_seq") or "")) - int(record.get("size") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0

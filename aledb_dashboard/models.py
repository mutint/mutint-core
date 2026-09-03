from django.db import models
from jsonfield import JSONField


class ObservedMutationCounts(models.Model):
    total = models.IntegerField(default=0)
    single_base_substitution = models.IntegerField(default=0)
    multiple_base_substitution = models.IntegerField(default=0)
    deletion = models.IntegerField(default=0)
    insertion = models.IntegerField(default=0)
    mobile_element_insertion = models.IntegerField(default=0)
    amplification = models.IntegerField(default=0)
    gene_conversion = models.IntegerField(default=0)
    inversion = models.IntegerField(default=0)
    intergenic = models.IntegerField(default=0)
    noncoding = models.IntegerField(default=0)
    pseudogene = models.IntegerField(default=0)
    synonymous = models.IntegerField(default=0)
    nonsynonymous = models.IntegerField(default=0)
    # Missing until the counts moved onto `Mutation.snp_type`. `annotator.py:470` has written
    # `nonsense` since the port landed, so these SNPs were being counted as something else.
    nonsense = models.IntegerField(default=0)
    unannotated = models.IntegerField(default=0)

    def __str__(self):
        return "ObservedMutationCounts Object with the following parameters: "+locals()


class UniqueMutationCounts(models.Model):
    total = models.IntegerField(default=0)
    single_base_substitution = models.IntegerField(default=0)
    multiple_base_substitution = models.IntegerField(default=0)
    deletion = models.IntegerField(default=0)
    insertion = models.IntegerField(default=0)
    mobile_element_insertion = models.IntegerField(default=0)
    amplification = models.IntegerField(default=0)
    gene_conversion = models.IntegerField(default=0)
    inversion = models.IntegerField(default=0)
    intergenic = models.IntegerField(default=0)
    noncoding = models.IntegerField(default=0)
    pseudogene = models.IntegerField(default=0)
    synonymous = models.IntegerField(default=0)
    nonsynonymous = models.IntegerField(default=0)
    # Missing until the counts moved onto `Mutation.snp_type`. `annotator.py:470` has written
    # `nonsense` since the port landed, so these SNPs were being counted as something else.
    nonsense = models.IntegerField(default=0)
    unannotated = models.IntegerField(default=0)
class SampleCounts(models.Model):
    ale_count = models.IntegerField(default=0)
    isolate_count = models.IntegerField(default=0)
    flask_count = models.IntegerField(default=0)

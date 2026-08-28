from django.test import TestCase
from aledb_seq.models import Mutation
from aledb_seq.models import ObservedMutation
from aledb_seq.models import ResequencingExperiment
from aledb_filter.models import AleExperimentFilter
from aledb_experiment.models import TechnicalReplicate,\
    Isolate,\
    Flask,\
    AleId,\
    AleExperiment,\
    Media,\
    FreezerBox
from aledb_experiment.models import Instrument
from aledb_filter.util import filter_observed_mutations


__author__ = 'Patrick Phaneuf'


class TestFilter(TestCase):

    def test_mutation_filter_one_gene_mutation_and_filter(self):
        ale_exp = AleExperiment.objects.create(instrument=Instrument.objects.create())
        ale_id = AleId.objects.create(ale_experiment=ale_exp, ale_id=1)
        media = Media.objects.create()
        flask = Flask.objects.create(ale_id=ale_id, flask_number=1, media=media)
        freezer_box = FreezerBox.objects.create()
        isolate = Isolate.objects.create(flask=flask,
                                          isolate_number=1,
                                          is_population=False,
                                          freezer_box=freezer_box)
        rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        reseq = ResequencingExperiment.objects.create(tech_rep=rep)
        mut = Mutation.objects.create(mutation_type="qwe",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneA")
        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut,
                                        frequency=1)

        filter_settings = AleExperimentFilter.objects.create(ale_experiment=ale_exp, ignored_genes="geneA")
        obs_muts = filter_observed_mutations(ObservedMutation.objects.all(), ale_exp.ale_id)
        self.assertEqual(len(obs_muts), 0)

    def test_mutation_filter_one_gene_mutation_many_gene_filter(self):
        ale_exp = AleExperiment.objects.create(instrument=Instrument.objects.create())
        ale_id = AleId.objects.create(ale_experiment=ale_exp, ale_id=1)
        media = Media.objects.create()
        flask = Flask.objects.create(ale_id=ale_id, flask_number=1, media=media)
        freezer_box = FreezerBox.objects.create()
        isolate = Isolate.objects.create(flask=flask,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)
        rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        reseq = ResequencingExperiment.objects.create(tech_rep=rep)
        mut = Mutation.objects.create(mutation_type="qwe",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="rcsF")
        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut,
                                        frequency=1)

        filter_settings = AleExperimentFilter.objects.create(ale_experiment=ale_exp,
                                                             ignored_genes="rcsF, –/–, rrlH, rrlD, rrsH, rrlA, gltP/yjcO, rsxC, eco/mqo")
        obs_muts = filter_observed_mutations(ObservedMutation.objects.all(), ale_exp.ale_id)
        self.assertEqual(len(obs_muts), 0)

    def test_mutation_filter_many_gene_mutation_one_gene_filter(self):
        ale_exp = AleExperiment.objects.create(instrument=Instrument.objects.create())
        ale_id = AleId.objects.create(ale_experiment=ale_exp, ale_id=1)
        media = Media.objects.create()
        flask = Flask.objects.create(ale_id=ale_id, flask_number=1, media=media)
        freezer_box = FreezerBox.objects.create()
        isolate = Isolate.objects.create(flask=flask,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)
        rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        reseq = ResequencingExperiment.objects.create(tech_rep=rep)
        mut = Mutation.objects.create(mutation_type="qwe",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneA, geneB")
        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut,
                                        frequency=1)

        filter_settings = AleExperimentFilter.objects.create(ale_experiment=ale_exp,
                                                             ignored_genes="geneA")

        obs_muts = filter_observed_mutations(ObservedMutation.objects.all(), ale_exp.ale_id)
        self.assertEqual(len(obs_muts), 1)

    def test_mutation_filter_many_gene_mutation_many_gene_filter(self):
        ale_exp = AleExperiment.objects.create(instrument=Instrument.objects.create())
        ale_id = AleId.objects.create(ale_experiment=ale_exp, ale_id=1)
        media = Media.objects.create()
        flask = Flask.objects.create(ale_id=ale_id, flask_number=1, media=media)
        freezer_box = FreezerBox.objects.create()
        isolate = Isolate.objects.create(flask=flask,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)
        rep = TechnicalReplicate.objects.create(isolate=isolate, tech_rep_number=1)
        reseq = ResequencingExperiment.objects.create(tech_rep=rep)
        mut = Mutation.objects.create(mutation_type="qwe",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneA, geneB")
        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut,
                                        frequency=1)

        filter_settings = AleExperimentFilter.objects.create(ale_experiment=ale_exp,
                                                             ignored_genes="geneB, geneA")

        obs_muts = filter_observed_mutations(ObservedMutation.objects.all(), ale_exp.ale_id)
        self.assertEqual(len(obs_muts), 0)

    def test_filter_observed_mutations_get_ale_exp_filter_from_observed_mutations(self):
        ale_exp1 = AleExperiment.objects.create(ale_id=1,
                                                name="exp1",
                                                instrument=Instrument.objects.create())
        ale_exp2 = AleExperiment.objects.create(ale_id=2,
                                                name="exp2",
                                                instrument=Instrument.objects.create())

        AleExperimentFilter.objects.create(ale_experiment=ale_exp1, ignored_genes="geneA")
        AleExperimentFilter.objects.create(ale_experiment=ale_exp2, ignored_genes="geneC")

        ale1 = AleId.objects.create(ale_id=1, ale_experiment=ale_exp1)
        ale2 = AleId.objects.create(ale_id=1, ale_experiment=ale_exp2)

        media = Media.objects.create()
        flask1 = Flask.objects.create(ale_id=ale1, media=media)
        flask2 = Flask.objects.create(ale_id=ale2, media=media)

        freezer_box = FreezerBox.objects.create()
        isolate1 = Isolate.objects.create(flask=flask1,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)
        isolate2 = Isolate.objects.create(flask=flask2,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)

        tech_rep1 = TechnicalReplicate.objects.create(isolate=isolate1)
        tech_rep2 = TechnicalReplicate.objects.create(isolate=isolate2)

        reseq1 = ResequencingExperiment.objects.create(tech_rep=tech_rep1)
        reseq2 = ResequencingExperiment.objects.create(tech_rep=tech_rep2)

        mut1 = Mutation.objects.create(mutation_type="SNP",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneA")
        mut2 = Mutation.objects.create(mutation_type="SNP",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneB")
        mut3 = Mutation.objects.create(mutation_type="SNP",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneC")

        ObservedMutation.objects.create(sequencing_experiment=reseq1,
                                        mutation=mut1,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq1,
                                        mutation=mut2,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq1,
                                        mutation=mut3,
                                        frequency=1)

        ObservedMutation.objects.create(sequencing_experiment=reseq2,
                                        mutation=mut1,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq2,
                                        mutation=mut2,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq2,
                                        mutation=mut3,
                                        frequency=1)

        obs_mut_qryset = ObservedMutation.objects.all()
        obs_muts = filter_observed_mutations(obs_mut_qryset)

        gene_mut_count_dict = {"geneA":0, "geneB":0, "geneC":0}
        for obs_mut in obs_muts:
            gene_mut_count_dict[obs_mut.mutation.gene] += 1

        self.assertEqual(gene_mut_count_dict["geneA"], 1)
        self.assertEqual(gene_mut_count_dict["geneB"], 2)
        self.assertEqual(gene_mut_count_dict["geneC"], 1)

    def test_each_experiments_gene_list_applies_only_to_its_own_rows(self):
        """Two experiments, two different ignored genes, the same three mutations in both.

        This tested a site-wide list as well until it was removed. What is left is the part
        worth pinning: an experiment's list must not reach another experiment's rows, and the
        two are ORed together across a queryset that spans both.
        """
        ale_exp1 = AleExperiment.objects.create(ale_id=1,
                                                name="exp1",
                                                instrument=Instrument.objects.create())
        ale_exp2 = AleExperiment.objects.create(ale_id=2,
                                                name="exp2",
                                                instrument=Instrument.objects.create())

        AleExperimentFilter.objects.create(ale_experiment=ale_exp1, ignored_genes="geneA")
        AleExperimentFilter.objects.create(ale_experiment=ale_exp2, ignored_genes="geneC")

        ale1 = AleId.objects.create(ale_id=1, ale_experiment=ale_exp1)
        ale2 = AleId.objects.create(ale_id=1, ale_experiment=ale_exp2)

        media = Media.objects.create()
        flask1 = Flask.objects.create(ale_id=ale1, media=media)
        flask2 = Flask.objects.create(ale_id=ale2, media=media)

        freezer_box = FreezerBox.objects.create()
        isolate1 = Isolate.objects.create(flask=flask1,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)
        isolate2 = Isolate.objects.create(flask=flask2,
                                         isolate_number=1,
                                         is_population=False,
                                         freezer_box=freezer_box)

        tech_rep1 = TechnicalReplicate.objects.create(isolate=isolate1)
        tech_rep2 = TechnicalReplicate.objects.create(isolate=isolate2)

        reseq1 = ResequencingExperiment.objects.create(tech_rep=tech_rep1)
        reseq2 = ResequencingExperiment.objects.create(tech_rep=tech_rep2)

        mut1 = Mutation.objects.create(mutation_type="SNP",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneA")
        mut2 = Mutation.objects.create(mutation_type="SNP",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneB")
        mut3 = Mutation.objects.create(mutation_type="SNP",
                                      position=1,
                                      sequence_change="zxcv",
                                      gene="geneC")

        ObservedMutation.objects.create(sequencing_experiment=reseq1,
                                        mutation=mut1,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq1,
                                        mutation=mut2,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq1,
                                        mutation=mut3,
                                        frequency=1)

        ObservedMutation.objects.create(sequencing_experiment=reseq2,
                                        mutation=mut1,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq2,
                                        mutation=mut2,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq2,
                                        mutation=mut3,
                                        frequency=1)

        obs_mut_qryset = ObservedMutation.objects.all()
        obs_muts = filter_observed_mutations(obs_mut_qryset)

        gene_mut_count_dict = {"geneA":0, "geneB":0, "geneC":0}
        for obs_mut in obs_muts:
            gene_mut_count_dict[obs_mut.mutation.gene] += 1

        # geneA is hidden in exp1 and kept in exp2; geneC the other way about. geneB is on
        # nobody's list and survives in both -- it was 0 here when a site-wide list could
        # reach across experiments.
        self.assertEqual(gene_mut_count_dict["geneA"], 1)
        self.assertEqual(gene_mut_count_dict["geneB"], 2)
        self.assertEqual(gene_mut_count_dict["geneC"], 1)

    def test_filter_observed_mutations_default_filter(self):
        ale_exp = AleExperiment.objects.create(ale_id=1,
                                                name="exp1",
                                                instrument=Instrument.objects.create())

        ale = AleId.objects.create(ale_id=1, ale_experiment=ale_exp)
        media = Media.objects.create()
        flask = Flask.objects.create(ale_id=ale, media=media)
        freezer_box = FreezerBox.objects.create()
        isolate = Isolate.objects.create(flask=flask,
                                          isolate_number=1,
                                          is_population=False,
                                          freezer_box=freezer_box)
        tech_rep = TechnicalReplicate.objects.create(isolate=isolate)
        reseq = ResequencingExperiment.objects.create(tech_rep=tech_rep)

        mut1 = Mutation.objects.create(mutation_type="SNP",
                                       position=1,
                                       sequence_change="zxcv",
                                       gene="geneA")
        mut2 = Mutation.objects.create(mutation_type="SNP",
                                       position=1,
                                       sequence_change="zxcv",
                                       gene="geneB")
        mut3 = Mutation.objects.create(mutation_type="SNP",
                                       position=1,
                                       sequence_change="zxcv",
                                       gene="geneC")

        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut1,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut2,
                                        frequency=1)
        ObservedMutation.objects.create(sequencing_experiment=reseq,
                                        mutation=mut3,
                                        frequency=1)

        obs_mut_qryset = ObservedMutation.objects.all()
        obs_muts = filter_observed_mutations(obs_mut_qryset)

        gene_mut_count_dict = {"geneA": 0, "geneB": 0, "geneC": 0}
        for obs_mut in obs_muts:
            gene_mut_count_dict[obs_mut.mutation.gene] += 1

        self.assertEqual(gene_mut_count_dict["geneA"], 1)
        self.assertEqual(gene_mut_count_dict["geneB"], 1)
        self.assertEqual(gene_mut_count_dict["geneC"], 1)

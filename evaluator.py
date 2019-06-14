
import pickle
from os.path import join

import numpy as np
import pandas as pd
from sklearn import metrics

from utils import (error, get_majority_label, info, numeric_to_string, one_hot,
                   warning)


class Evaluator:
    """Class to produce evaluation measures
    """

    # performance containers, in the format type-measure-aggregation-stat
    performance = {}
    cw_performance = {}
    majority_label = None

    # predictions storage
    predictions = None

    # available measures, aggregations and run types
    singlelabel_measures = ["precision", "recall", "f1-score", "accuracy"]
    stats = ["mean", "var", "std", "folds"]
    run_types = ["random", "majority", "run"]
    multilabel_measures = ["ap", "roc_auc"]
    multiclass_aggregations = ["macro", "micro", "classwise", "weighted"]

    # defined in runtime
    measures = None

    # print opts
    print_precision = "{:.03f}"
    do_multilabel = None

    # error analysis
    error_analysis = None
    error_analysis_types = ["top", "bottom"]
    top_k = 3

    # constructor
    def __init__(self, config):
        """Evaluator constructor method"""
        self.config = config
        # minor inits
        self.predictions = {rt: [] for rt in self.run_types}

    def set_labels(self, test_labels, num_labels, do_multilabel):
        """Label setter method"""
        self.do_multilabel = do_multilabel
        self.test_labels = test_labels
        if not self.do_multilabel:
            self.test_labels = np.squeeze(np.concatenate(test_labels))
        self.num_labels = num_labels

    def check_sanity(self):
        """Evaluator sanity checking function"""
        # set measures type wrt labelling problem
        self.measures = self.multilabel_measures if self.do_multilabel else self.singlelabel_measures

        # set output options
        self.preferred_types = self.config.print.run_types if self.config.print.run_types else self.run_types
        self.preferred_measures = self.config.print.measures if self.config.print.measures else []
        self.preferred_aggregations = self.config.print.aggregations if self.config.print.aggregations else self.multiclass_aggregations
        self.preferred_stats = self.config.print.stats if self.config.print.stats else self.stats
        self.top_k = self.config.print.top_k
        error("Invalid value for top-k printing: {}".format(self.top_k), self.top_k > 0)

        # all measures, if no subset is preferred
        if not self.preferred_measures:
            self.preferred_measures = self.measures
        else:
            # restrict as per labelling and sanity checks
            matching_measures = set(self.preferred_measures).intersection(self.measures)
            if not matching_measures:
                error("Invalid preferred measures: {} for {} setting.".format(
                    self.preferred_measures, "multilabel" if self.do_multilabel else "single-label"))
            self.preferred_measures = matching_measures

        # configure evaluation measures
        self.configure_evaluation_measures()

    # initialize evaluation containers and preferred evaluation printage
    def configure_evaluation_measures(self):
        """Method to initialize evaluation measure containers"""
        for run_type in self.run_types:
            self.performance[run_type] = {}
            for measure in self.measures:
                self.performance[run_type][measure] = {}
                if self.do_multilabel:
                    for stat in self.stats:
                        self.performance[run_type][measure] = {}
                        self.performance[run_type][measure]["folds"] = []
                else:
                    for aggr in self.multiclass_aggregations:
                        self.performance[run_type][measure][aggr] = {}
                        for stat in self.stats:
                            self.performance[run_type][measure][aggr][stat] = None
                        self.performance[run_type][measure][aggr]["folds"] = []

        # remove undefined combos
        if not self.do_multilabel:
            for run_type in self.run_types:
                for aggr in [x for x in self.multiclass_aggregations if x not in ["macro", "classwise"]]:
                    del self.performance[run_type]["accuracy"][aggr]

        # sanity
        undefined = [x for x in self.preferred_types if x not in self.run_types]
        if undefined:
            error("undefined run type(s) in: {}, availables are: {}".format(undefined, self.run_types))
        undefined = [x for x in self.preferred_measures if x not in self.measures + self.multilabel_measures]
        if undefined:
            error("Undefined measure(s) in: {}, availables are: {}".format(undefined, self.measures + self.multilabel_measures))
        undefined = [x for x in self.preferred_aggregations if x not in self.multiclass_aggregations]
        if undefined:
            error("Undefined aggregation(s) in: {}, availables are: {}".format(undefined, self.multiclass_aggregations))

    # aggregated evaluation measure function shortcuts
    def get_pre_rec_f1(self, preds, metric, num_labels, gt=None):
        """Function to compute precision, recall and F1-score"""
        if gt is None:
            gt = self.test_labels
        # expected column structure is label1 label2 ... labelN microavg macroavg weightedavg
        cr = pd.DataFrame.from_dict(metrics.classification_report(gt, preds, output_dict=True))
        # get classwise, micro, macro, weighted, defaulting non-precited classes to zero values
        predicted_classes = cr.columns.to_list()[:-3]
        cw = np.zeros(num_labels, np.float32)
        for class_index in predicted_classes:
            cw[int(class_index)] = cr[class_index][metric]
        mi = cr["micro avg"][metric]
        ma = cr["macro avg"][metric]
        we = cr["weighted avg"][metric]
        return cw, mi, ma, we

    # get average accuracy
    def compute_accuracy(self, preds, gt=None):
        if gt is None:
            gt = self.test_labels
        return metrics.accuracy_score(gt, preds)

    # get class-wise accuracies
    def compute_classwise_accuracy(self, preds, gt=None):
        if gt is None:
            gt = self.test_labels
        cm = metrics.confusion_matrix(gt, preds)
        cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
        return cm.diagonal()

    def print_performance(self, run_type, measure, aggr=None):
        """performance printing function, checking respective settings and inputs for wether to print"""
        # print the combination, if it's in the prefered stuff to print
        if all([run_type in self.preferred_types, measure in self.preferred_measures, aggr is None or aggr in self.preferred_aggregations]):
            scores_str = self.get_score_stats_string(self.performance[run_type][measure][aggr])
            # print
            header = " ".join(["{:10}".format(x) for x in [run_type, aggr, measure, scores_str] if x is not None])
            info("{} : {}".format(header, scores_str))

    # print performance across folds and compute foldwise aggregations
    def report_overall_results(self, folds, write_folder):
        """Function to report learner results
        """
        info("==============================")
        self.show_label_distribution(self.test_labels)
        self.analyze_overall_errors()

        info("{} {} performance {} across all [{}] folds:".format("/".join(self.preferred_types), "/".join(self.preferred_measures), "/".join(self.preferred_stats), folds))
        info("==============================")
        for run_type in self.run_types:
            for measure in self.measures:
                if not self.do_multilabel:
                    for aggr in self.multiclass_aggregations:
                        if aggr not in self.performance[run_type][measure]:
                            continue
                        # calculate the foldwise statistics
                        self.performance[run_type][measure][aggr] = self.calc_fold_score_stats(self.performance[run_type][measure][aggr])
                        # print if it's required by the settings
                        self.print_performance(run_type, measure, aggr)
                        # # print the combination, if it's in the prefered stuff to print
                        # if all([run_type in self.preferred_types, measure in self.preferred_measures, aggr in self.preferred_aggregations]):
                        #     scores_str = self.get_score_stats_string(self.performance[run_type][measure][aggr])
                        #     info("{:10} {:10} {:10} : {}".format(run_type, aggr, measure, scores_str))
                else:
                    # multilabel setting: different measure, no classwise aggregations
                    for measure in self.multilabel_measures:
                        self.performance[run_type][measure] = self.calc_fold_score_stats(self.performance[run_type][measure])
                        self.print_performance(run_type, measure)
                        # # print, if it's prefered
                        # if all([run_type in self.preferred_types, measure in self.preferred_measures]):
                        #     scores_str = self.get_score_stats_string(self.performance[run_type][measure])
                        #     info("{:10} {:10} : {}".format(run_type, measure, scores_str))

        if write_folder is not None:
            # write the results in csv in the results directory
            # entries in a run_type - measure configuration list are the foldwise scores, followed by the mean
            df = pd.DataFrame.from_dict(self.performance)
            df.to_csv(join(write_folder, "results.txt"))
            with open(join(write_folder, "results.pickle"), "wb") as f:
                pickle.dump(df, f)

    # print performance of the latest run
    def print_run_performance(self, current_run_descr, fold_index=0):
        info("---------------")
        info("Test results for {}:".format(current_run_descr))
        for rtype in self.preferred_types:
            if not self.do_multilabel:
                for measure in self.preferred_measures:
                    for aggr in self.preferred_aggregations:
                        # don't print classwise results or unedfined aggregations
                        if aggr not in self.performance[rtype][measure]:
                            continue
                        container = self.performance[rtype][measure][aggr]
                        if not container:
                            continue
                        str_value = numeric_to_string(self.performance[rtype][measure][aggr]["folds"][fold_index], self.print_precision)
                        info(("{}| {} {}: {}").format(rtype, aggr, measure, str_value))
            else:
                for measure in self.multilabel_measures:
                    container = self.performance[rtype][measure]
                    if not container:
                        continue
                    info(("{}| {}:" + self.print_precision).format(rtype, measure, self.performance[rtype][measure]["folds"][fold_index]))
        info("---------------")

    # compute scores and append to per-fold lists
    def evaluate_predictions(self, run_type, preds_proba):
        # loop thresholds & amax, get respective TPs, FPs, etc
        # evaluate metrics there, and multilabel evals with these.

        # sanity
        if self.num_labels != preds_proba.shape[-1]:
            error("Attempted to evaluated {}-dimensional predictions against {} labels".format(preds_proba.shape[-1], self.num_labels))

        if self.do_multilabel:
            onehot_gt = one_hot(self.test_labels, self.num_labels)

            # average precision
            ap = metrics.average_precision_score(onehot_gt, preds_proba)
            rocauc = metrics.roc_auc_score(onehot_gt, preds_proba)

            self.performance[run_type]["ap"]["folds"].append(ap)
            self.performance[run_type]["roc_auc"]["folds"].append(rocauc)
        else:
            # single-label
            preds_amax = np.argmax(preds_proba, axis=1)
            # get prec, rec, f1
            for measure in [x for x in self.measures if x != "accuracy"]:
                cw, ma, mi, ws = self.get_pre_rec_f1(preds_amax, measure, self.num_labels)
                self.performance[run_type][measure]["classwise"]["folds"].append(cw)
                self.performance[run_type][measure]["macro"]["folds"].append(ma)
                self.performance[run_type][measure]["micro"]["folds"].append(mi)
                self.performance[run_type][measure]["weighted"]["folds"].append(ws)

            # get accuracies
            acc, cw_acc = self.compute_accuracy(preds_amax), self.compute_classwise_accuracy(preds_amax)
            self.performance[run_type]["accuracy"]["classwise"]["folds"].append(cw_acc)
            self.performance[run_type]["accuracy"]["macro"]["folds"].append(acc)

    # show labels distribution
    def show_label_distribution(self, labels=None):
        if labels is None:
            labels = self.test_labels
        # show label distribution
        hist = {}
        for lblset in labels:
            if self.do_multilabel:
                for lbl in lblset:
                    lbl = int(lbl)
                    if lbl not in hist:
                        hist[lbl] = 0
                    hist[lbl] += 1
            else:
                if lblset not in hist:
                    hist[lblset] = 0
                hist[lblset] += 1
        info("Label distribution:")
        sorted_labels = sorted(hist.keys())
        for lbl in sorted_labels:
            count = hist[lbl]
            info("Label {} : {}".format(lbl, count))

    # evaluate predictions and add baselines
    def evaluate_learning_run(self, predictions):
        # # get multiclass performance
        # for av in ["macro", "micro"]:
        #     auc, ap = self.get_roc(predictions, average=av)
        #     self.performance["run"]["AP"][av] = ap
        #     self.performance["run"]["AUC"][av] = auc

        if len(predictions) != len(self.test_labels):
            error("Inconsistent shapes of predictions: {} and labels: {} lengths during evaluation"
                  .format(len(predictions), len(self.test_labels)))
        # compute single-label baselines
        # add run performance wrt argmax predictions
        self.evaluate_predictions("run", predictions)
        # majority classifier
        if self.majority_label is None:
            self.majority_label = get_majority_label(self.test_labels, self.num_labels, self.do_multilabel)
        majpred = np.zeros(predictions.shape, np.float32)
        majpred[:, self.majority_label] = 1.0
        self.evaluate_predictions("majority", majpred)
        # random classifier
        randpred = np.random.rand(*predictions.shape)
        self.evaluate_predictions("random", randpred)

        self.predictions["run"].append(predictions)
        self.predictions["random"].append(randpred)
        self.predictions["majority"].append(majpred)

    # applies the threshold to the probabilistic predictions, extracting decision indices
    def apply_decision_threshold(self, proba, thresh):
        decisions = []
        for row in proba:
            idxs = np.where(row > thresh)
            decisions.append(idxs)
        return decisions

    # compute statistics across folds
    def calc_fold_score_stats(self, container):
        # set foldwise scores to an nparray
        container["folds"] = np.asarray(container["folds"])
        for key, func in [("mean", np.mean), ("var", np.var), ("std", np.std)]:
            container[key] = func(container["folds"], axis=0)
        return container

    # get a printable format of evaluation
    def get_score_stats_string(self, container):
        scores_str = []
        for stat in self.preferred_stats:
            scores_str.append(numeric_to_string(container[stat], self.print_precision))
        return " ".join(scores_str)

    # analyze overall error of the run
    def analyze_overall_errors(self, top_k=3):
        """Method to generate an error analysis over folds
        - label-wise ranking: extract best/worst labels, average across instances
        - instance-wise ranking: extract best/worst instances, average across labels
          - run type (e.g. regular, random, majority)
          - evaluation measure (e.g. accuracy, f1)
          - fold-wise aggregation evaluation measure stat (mean, std, variance)

        for labels: use classwise, mean per fold
        for instances: have to save test predictions, mean per fold
        for across runs, have to use large_run externally


        - print top / bottom K
        """
        res = {"instances": {}, "labels": {}}

        if not self.do_multilabel:
            for run_type in self.predictions:
                # instance-wise
                aggregate = np.zeros((len(self.test_labels),), np.float32)
                for preds in self.predictions[run_type]:
                    # get true / false predictions
                    correct_preds = np.where(np.equal(np.argmax(preds, axis=1), self.test_labels))
                    aggregate[correct_preds] += 1
                # average
                aggregate /= len(self.predictions[run_type])
                # sort scores and instance indexes
                ranked_scores_idxs = sorted(list(zip(aggregate, list(range(len(aggregate))))), key=lambda x: x[0], reverse=True)
                res["instances"][run_type] = {"top": ranked_scores_idxs[:top_k], "bottom": ranked_scores_idxs[-top_k:]}

                # label-wise
                lbl_top = min(top_k, self.num_labels)
                res["labels"][run_type] = {}
                for measure in self.measures:
                    scores_cw = self.performance[run_type][measure]['classwise']['folds']
                    # average accross folds
                    scores_cw = sum(scores_cw) / len(scores_cw)
                    # rank
                    ranked_scores_idxs = sorted(list(zip(scores_cw, list(range(len(scores_cw))))), key=lambda x: x[0], reverse=True)
                    res["labels"][run_type][measure] = {"top": ranked_scores_idxs[:lbl_top], "bottom": ranked_scores_idxs[-lbl_top:]}
        else:
            pass

        self.error_analysis = res
        self.print_error_analysis()

    def print_error_analysis(self):
        """Function that outputs the computed error analysis
        """
        if not self.config.print.error_analysis:
            return
        info("Fold-average top-{} instances/labels:".format(self.top_k))
        info("---------------------------")
        for run_type in self.run_types:
            # print in format instance1, instance2, ...
            for an_type in self.error_analysis_types:
                info("accuracy {}: {:7} instances: ({}): ({})".format(run_type, an_type, " ".join("{:.0f}".format(x[1]) for x in self.error_analysis["instances"][run_type][an_type]),
                                                                      " ".join("{:1.3f}".format(x[0]) for x in self.error_analysis["instances"][run_type][an_type])))
            for measure in self.performance[run_type]:
                info("{:10} {}: {:7} labels: ({}): ({})".format(measure, run_type, an_type, " ".join("{:.0f}".format(x[1]) for x in self.error_analysis["labels"][run_type][measure][an_type]),
                                                                " ".join("{:1.3f}".format(x[0]) for x in self.error_analysis["labels"][run_type][measure][an_type])))

    # analyze error wrt selected parameters
    def analyze_errors(self, run_type, measure, aggregation):
        """Method to generate an error analysis
        - rank labels / instances
        - print top / bottom K
        """
        # instance-wise
        if not self.do_multilabel:
            # label-wise
            pass
        # import ipdb; ipdb.set_trace()
        scores = sorted(self.performance[run_type][measure][aggregation])

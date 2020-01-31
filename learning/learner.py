from copy import deepcopy
from os import makedirs
from os.path import dirname, exists, join

import numpy as np
from sklearn.model_selection import KFold, ShuffleSplit

from bundle.bundle import BundleList
from bundle.datatypes import Labels, Vectors
from component.component import Component
from learning.evaluator import Evaluator
from learning.sampling import Sampler
from learning.validation import ValidationSetting
from utils import error, info, read_pickled, tictoc, write_pickled


"""
Abstract class representing a learning model
"""


class Learner(Component):

    component_name = "learner"
    name = "learner"
    save_dir = "models"
    folds = None
    fold_index = 0
    evaluator = None
    sequence_length = None
    input_aggregation = None
    train, test = None, None

    test_instance_indexes = None
    validation = None

    allow_model_loading = None
    allow_prediction_loading = None

    train_embedding = None

    def __init__(self):
        """Generic learning constructor
        """
        # initialize evaluation
        Component.__init__(self, consumes=[Vectors.name, Labels.name])
        self.can_be_final = True

    # input preproc
    def process_input(self, data):
        return data

    def count_samples(self):
        self.num_train, self.num_test = map(len, [self.train_index, self.test_index])

    def read_config_variables(self):
        """Shortcut function for readding a load of config variables"""
        self.allow_prediction_loading = self.config.misc.allow_prediction_loading
        self.allow_model_loading = self.config.misc.allow_model_loading

        self.sequence_length = self.config.learner.sequence_length
        self.train_embedding = self.config.learner.train_embedding

        self.results_folder = self.config.folders.results
        self.models_folder = join(self.results_folder, "models")

        self.batch_size = self.config.train.batch_size
        self.epochs = self.config.train.epochs
        self.early_stopping_patience = self.config.train.early_stopping_patience
        self.folds = self.config.train.folds
        self.validation_portion = self.config.train.validation_portion
        self.do_folds = self.folds and self.folds > 1
        self.do_validate_portion = self.validation_portion is not None and self.validation_portion > 0.0
        self.validation_exists = (self.do_folds or self.do_validate_portion)

        self.seed = self.config.misc.seed

        self.sampling_method, self.sampling_ratios = self.config.train.sampling_method, self.config.train.sampling_ratios
        self.do_sampling = self.sampling_method is not None

    def check_sanity(self):
        """Sanity checks"""
        # check data for nans
        if np.size(np.where(np.isnan(self.embeddings))[0]) > 0:
            error("NaNs exist in data:{}".format(np.where(np.isnan(self.embeddings))))
        # validation configuration
        if self.do_folds and self.do_validate_portion:
            error("Specified both folds {} and validation portion {}.".format(
                self.folds, self.validation_portion))
        if not (self.validation_exists or self.test_data_available()):
            error("No test data or cross/portion-validation setting specified.")
        self.evaluator.check_sanity()

    def configure_validation_setting(self):
        """Initialize validation setting"""
        self.validation = ValidationSetting(self.folds, self.validation_portion, self.test_data_available())
        self.validation.assign_data(self.embeddings, train_index=self.train_index, test_index=self.test_index)

    def configure_sampling(self):
        """No label-agnostic sampling"""
        # No label-agnostic sampling available
        pass

    def attach_evaluator(self):
        self.evaluator = Evaluator(self.config, self.validation.use_for_testing)

    def make(self):
        # get handy variables
        self.read_config_variables()
        np.random.seed(self.seed)
        self.count_samples()

        self.input_dim = self.embeddings.shape[-1]
        error("Input none dimension.", self.input_dim is None)

        info("Learner data: embeddings: {} train idxs: {} test idxs: {}".format(
            self.embeddings.shape, len(self.train_index), len(self.test_index)))

        info("Created learning: {}".format(self))

    def get_existing_predictions(self):
        path = self.validation.modify_suffix(
            join(self.results_folder, "{}".format(
                self.name))) + ".predictions.pickle"
        return read_pickled(path) if exists(path) else (None, None)

    def get_existing_trainval_indexes(self):
        """Check if the current training run is already completed."""
        trainval_file = self.get_trainval_serialization_file()
        if exists(trainval_file):
            info("Training {} with input data: {} samples on LOADED existing {}" .format(self.name, self.num_train, self.validation))
            idx = read_pickled(trainval_file)
            self.validation.check_indexes(idx)
            max_idx = max([np.max(x) for tup in idx for x in tup])
            if max_idx >= self.num_train:
                error(
                    "Mismatch between max instances in training data ({}) and loaded max index ({})."
                    .format(self.num_train, max_idx))

    def get_existing_model_path(self):
        path = self.get_current_model_path()
        return path if exists(path) else None

    def test_data_available(self):
        return len(self.test_index) > 0

    # function to retrieve training data as per the existing configuration
    def get_trainval_indexes(self):
        trainval_idx = None
        # get training / validation indexes
        if self.allow_model_loading:
            ret = self.get_existing_model_path()
            if ret:
                trainval_idx, self.existing_model_paths = ret
        if not trainval_idx:
            trainval_idx = self.compute_trainval_indexes()

        # handle indexes for multi-instance data
        if self.sequence_length > 1:
            self.validation.set_trainval_label_index(deepcopy(trainval_idx))
            # trainval_idx = self.expand_index_to_sequence(trainval_idx)
        return trainval_idx

    def show_train_statistics(self, train_labels, val_labels):
        pass

    def acquire_trained_model(self, train_index, val_index, train_labels, val_labels):
        """Trains the learning model or load an existing instance from a persisted file."""
        with tictoc("Training run [{}] on {} training and {} val data.".format(self.validation, self.num_train, len(val_index) if val_index is not None else "[none]")):
            model = None
            # check if a trained model already exists
            if self.allow_model_loading:
                model = self.load_model()
            if not model:
                model = self.train_model(train_index, self.embeddings, train_labels, val_index, val_labels)
                # create directories
                makedirs(self.models_folder, exist_ok=True)
                self.save_model(model)
            else:
                info("Skipping training due to existing model successfully loaded.")
        return model

    # perfrom a train-test loop
    def do_traintest(self):
        with tictoc("Entire learning run",
                    do_print=self.do_folds,
                    announce=False):

            # get trainval data
            train_val_idxs = self.get_trainval_indexes()

            # keep track of models' test performances and paths wrt selected metrics
            model_paths = []

            # iterate over required runs as per the validation setting
            for iteration_index, trainval in enumerate(train_val_idxs):
                # get instance indexes and labels
                train_index, val_index, test_index, test_instance_indexes = self.validation.get_run_data(iteration_index, trainval)
                train_labels, val_labels, test_labels = self.validation.get_run_labels(iteration_index, trainval)

                # show training data statistics
                self.show_train_statistics(train_labels, val_labels)

                # make a sample count for printing
                self.num_train, self.num_val, self.num_test = [len(x) for x in (train_index, val_index, test_index)]
                # self.count_samples(train_index, val_index, test_index)

                # for evaluation, pass all information of the current (maybe cross-validated) run testing, gotta keep the reference labels updated, if any
                self.evaluator.update_reference(train_index=train_index, test_index=test_index, embeddings=self.embeddings)

                # check if the run is completed already and load existing results, if allowed
                model, predictions = None, None
                if self.allow_prediction_loading:
                    predictions, test_instance_indexes = self.load_existing_predictions(test_instance_indexes, test_labels)

                # train the model
                if predictions is None:
                    model = self.acquire_trained_model(train_index, val_index, train_labels, val_labels)
                else:
                    info("Skipping training due to existing predictions successfully loaded.")

                # test and evaluate the model
                with tictoc("Testing run [{}] on {} test data.".format(self.validation.descr, self.num_test)):
                    self.do_test_evaluate(model, test_index, self.embeddings, test_instance_indexes, test_labels, predictions)
                    model_paths.append(self.get_current_model_path())

                if self.validation_exists and self.validation.use_for_testing:
                    self.test, self.test_labels, self.test_instance_indexes = [], [], None

                # wrap up the current validation iteration
                self.validation.conclude_iteration()

            # end of validation loop
            if self.config.print.label_distribution:
                self.evaluator.show_reference_label_distribution()
            # report results across entire training
            self.evaluator.report_overall_results(self.validation.descr, len(self.train_index), self.results_folder)

    # evaluate a model on the test set
    def do_test_evaluate(self,
                         model,
                         test_index,
                         embeddings,
                         test_instance_indexes,
                         test_labels=None,
                         predictions=None):
        if predictions is None:
            # evaluate the model
            error("No test data supplied!", len(test_index) == 0)
            predictions = self.test_model(test_index, embeddings, model)
        # get performances
        self.evaluator.evaluate_learning_run(predictions, test_instance_indexes)
        if self.do_folds and self.config.print.folds:
            self.evaluator.print_run_performance(self.validation.descr, self.validation.current_fold)
        # write fold predictions
        predictions_file = self.validation.modify_suffix(join(self.results_folder, "{}".format(self.name))) + ".predictions.pickle"
        write_pickled(predictions_file, [predictions, test_instance_indexes])

    def get_current_model_path(self):
        return self.validation.modify_suffix(
            join(self.results_folder, "models", "{}".format(self.name))) + ".model"

    def get_trainval_serialization_file(self):
        sampling_suffix = "{}.trainvalidx.pickle".format(
            "" if not self.do_sampling else "{}_{}".
            format(self.sampling_method, "_".
                   join(map(str, self.sampling_ratios))))
        return self.validation.modify_suffix(
            join(self.results_folder, "{}".format(
                self.name))) + sampling_suffix

    # produce training / validation splits, with respect to sample indexes
    def compute_trainval_indexes(self):
        if not self.validation_exists:
            # return all training indexes, no validation
            return [(np.arange(len(self.train_index)), np.arange(0))]

        trainval_serialization_file = self.get_trainval_serialization_file()

        if self.do_folds:
            info("Splitting {} with input data: {} samples, on {} folds"
                .format(self.name, self.num_train, self.folds))
            splitter = KFold(self.folds, shuffle=True, random_state=self.seed)

        if self.do_validate_portion:
            info("Splitting {} with input data: {} samples on a {} validation portion"
                .format(self.name, self.num_train, self.validation_portion))
            splitter = ShuffleSplit(
                n_splits=1,
                test_size=self.validation_portion,
                random_state=self.seed)

        # generate. for multilabel K-fold, stratification is not usable
        splits = list(splitter.split(np.zeros(self.num_train)))

        # do sampling processing
        if self.do_sampling:
            smpl = Sampler()
            splits = smpl.sample()

        # save and return the splitter splits
        makedirs(dirname(trainval_serialization_file), exist_ok=True)
        write_pickled(trainval_serialization_file, splits)
        return splits

    def is_supervised(self):
        error("Attempted to access abstract learner is_supervised() method")
        return None

    def save_model(self, model):
        path = self.get_current_model_path()
        info("Saving model to {}".format(path))
        write_pickled(path, model)

    def load_model(self):
        path = self.get_current_model_path()
        if not path or not exists(path):
            return None
        info("Loading existing learning model from {}".format(path))
        return read_pickled(self.get_current_model_path())

    # region: component functions
    def run(self):
        self.process_component_inputs()
        self.make()
        self.configure_validation_setting()
        self.attach_evaluator()
        self.configure_sampling()
        self.check_sanity()
        self.do_traintest()
        self.outputs.set_vectors(Vectors(vecs=self.evaluator.predictions))

    def process_component_inputs(self):
        # get data and labels
        error("Learner needs at least two-input bundle input list.",
              type(self.inputs) is not BundleList)
        error("{} needs vector information.".format(self.component_name),
              not self.inputs.has_vectors())

        self.train_index, self.test_index = (np.squeeze(np.asarray(x)) for x in self.inputs.get_indices(single=True).instances)
        self.embeddings = self.inputs.get_vectors(single=True).instances
        if self.is_supervised():
            error("{} needs label information.".format(self.component_name), not self.inputs.has_labels())
            self.train_labels, self.test_labels = self.inputs.get_labels(single=True).instances
            if len(self.train_index) != len(self.train_labels):
                error(f"Train data-label instance number mismatch: {len(self.train_index)} data and {len(self.train_labels)}")
            if len(self.test_index) != len(self.test_labels):
                error(f"Test data-label instance number mismatch: {len(self.test_index)} data and {len(self.test_labels)}")
            self.multilabel_input = self.inputs.get_labels(single=True).multilabel

    def load_existing_predictions(self, current_test_instance_indexes, current_test_labels):
        # get predictions and instance indexes they correspond to
        existing_predictions, existing_instance_indexes = self.get_existing_predictions()
        if existing_predictions is not None:
            info("Loaded existing predictions.")
            error("Different instance indexes loaded than the ones generated.",
                not np.all(np.equal(existing_instance_indexes, current_test_instance_indexes)))
            existing_test_labels = self.validation.get_test_labels(
                current_test_instance_indexes)
            error(
                "Different instance labels loaded than the ones generated.",
                not np.all(
                    np.equal(existing_test_labels, current_test_labels)))
        return existing_predictions, existing_instance_indexes

    def get_data_from_index(self, index, embeddings):
        """Get data index from the embedding matrix"""
        if np.squeeze(index).ndim > 1:
            if self.input_aggregation is None and self.sequence_length < 2:
                # if we have multi-element index, there has to be an aggregation method defined for the learner.
                error("Learner [{}] has no defined aggregation and is not sequence-capable, but the input index has shape {}".format(self.name, index.shape))
        return embeddings[index] if len(index) > 0 else None

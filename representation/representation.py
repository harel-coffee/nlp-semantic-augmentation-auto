import numpy as np

import defs
from bundle.datatypes import Indices, Text, Vectors
from component.component import Component
from serializable import Serializable
from utils import debug, error, info, shapes_list


class Representation(Serializable):
    component_name = "representation"
    dir_name = "representation"
    compatible_aggregations = []
    compatible_sequence_lengths = []
    sequence_length = 1
    dataset_vectors = None

    data_names = ["dataset_vectors", "elements_per_instance", "embeddings"]

    @staticmethod
    def get_available():
        return [cls.name for cls in Representation.__subclasses__()]

    def __init__(self):
        """Constructor"""
        Component.__init__(self, consumes=Text.name, produces=Vectors.name)

    def populate(self):
        Serializable.__init__(self, self.dir_name)
        # check for serialized mapped data
        self.set_serialization_params()
        # set required resources
        self.set_resources()
        # fetch the required data
        self.acquire_data()
        # restore name, maybe
        if self.multiple_config_names:
            self.configure_name()
            self.set_serialization_params()
            # self.set_params()
            # self.set_name()
            # Component.configure_name(self)
            # self.check_params()
            info("Restored representation name to {}".format(self.name))

    # region # serializable overrides

    def handle_preprocessed(self, preprocessed):
        self.loaded_preprocessed = True
        self.dataset_vectors, self.elements_per_instance, self.embeddings = [preprocessed[n] for n in Representation.data_names]
        debug("Read preprocessed dataset embeddings shapes: {}".format(shapes_list(self.dataset_vectors)))

    # add exra representations-specific serialization paths
    def set_additional_serialization_sources(self):
        # compute names
        aggr = "".join(list(map(str, [self.config.representation.aggregation] + [self.sequence_length])))
        self.serialization_path_aggregated = "{}/{}.aggregated_{}.pickle".format(self.serialization_dir, self.name, aggr)
        self.add_serialization_source(self.serialization_path_aggregated, handler=self.handle_aggregated)

    def handle_aggregated(self, data):
        self.handle_preprocessed(data)
        self.loaded_aggregated = True
        debug("Read aggregated embeddings shapes: {}, {}".format(*shapes_list(self.dataset_vectors)))

    def handle_raw(self, raw_data):
        pass

    def fetch_raw(self, path):
        # assume embeddings are dataframes
        return None

    def preprocess(self):
        pass

    # endregion

    # region # getter functions
    def get_zero_pad_element(self):
        return np.zeros((1, self.dimension), np.float32)

    def get_data(self):
        return self.dataset_vectors

    def get_name(self):
        return self.name

    def get_dimension(self):
        return self.dimension

    def get_vectors(self):
        return self.dataset_vectors

    def get_elements_per_instance(self):
        return self.elements_per_instance
    # endregion

    # shortcut for reading configuration values
    def set_params(self):
        self.aggregation = self.config.representation.aggregation
        self.dimension = self.config.representation.dimension
        self.dataset_name = self.source_name

        self.sequence_length = self.config.representation.sequence_length
        self.do_train_vectors = self.config.representation.train

    def check_params(self):
        if self.aggregation not in self.compatible_aggregations:
            error("[{}] aggregation incompatible with {}. Compatible ones are: {}!".format(self.aggregation, self.base_name, self.compatible_aggregations))
        error("Unset sequence length compatibility for representation {}".format(self.base_name), not self.compatible_sequence_lengths)
        if defs.get_sequence_length_type(self.sequence_length) not in self.compatible_sequence_lengths:
            error("Incompatible sequence length {} with {}. Compatibles are {}".format(
                self.sequence_length, self.base_name, self.compatible_sequence_lengths))

    @staticmethod
    def generate_name(config, input_name):
        return "{}_{}_dim{}".format(config.representation.name, input_name, config.representation.dimension)

    # name setter function, exists for potential overriding
    def set_name(self):
        self.name = Representation.generate_name(self.config, self.source_name)

    # set one element per instance
    def set_constant_elements_per_instance(self, num=1):
        if not self.dataset_vectors:
            error("Attempted to set constant epi before computing dataset vectors.")
        self.elements_per_instance = [np.asarray([num for _ in ds], np.int32) for ds in self.dataset_vectors]

    # data getter for semantic processing
    def process_data_for_semantic_processing(self, train, test):
        return train, test

    # abstracts
    def aggregate_instance_vectors(self):
        error("Attempted to run abstract aggregate_instance_vectors for {}".format(self.name))

    def compute_dense(self):
        error("Attempted to run abstract compute_dense for {}".format(self.name))

    def map_text(self):
        error("Attempted to access abstract function map_text of {}".format(self.name))

    # region  # chain methods

    def configure_name(self):
        self.source_name = self.inputs.get_source_name()
        self.set_params()
        self.set_name()
        self.check_params()
        Component.configure_name(self)

    def run(self):
        self.populate()
        self.process_component_inputs()
        self.map_text()
        self.compute_dense()
        self.aggregate_instance_vectors()
        self.outputs.set_vectors(Vectors(vecs=self.embeddings))
        self.outputs.set_indices(Indices(self.dataset_vectors))

    def process_component_inputs(self):
        if self.loaded_aggregated or self.loaded_preprocessed:
            return
        error("{} requires a text input.".format(self.name), not self.inputs.has_text())
        self.text, self.vocabulary = self.inputs.get_text().instances, self.inputs.get_text().vocabulary

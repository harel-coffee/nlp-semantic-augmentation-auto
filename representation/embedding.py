import numpy as np
import pandas as pd
from pandas.errors import ParserError

from os.path import join
import defs
from representation.representation import Representation
from utils import (debug, error, get_shape, info, realign_embedding_index,
                   shapes_list, warning)


class Embedding(Representation):
    name = ""
    words = []
    vector_indices = None
    embeddings = None
    words_to_numeric_idx = None
    dimension = None
    # word - word_index map
    embedding_vocabulary_index = {}
    undefined_element_index = None

    # region # serializable overrides
    def set_resources(self):
        csv_mapping_name = "{}/{}.csv".format(self.raw_data_dir, self.base_name)
        self.resource_paths.append(csv_mapping_name)
        self.resource_read_functions.append(self.read_raw_embedding_mapping)
        self.resource_handler_functions.append(lambda x: x)

        # need the raw embeddings even if processed embedding data is available
        # if self.config.has_semantic() and self.config.name == "context":
        #     # need the raw embeddings even if processed embedding data is available
        #     self.resource_always_load_flag.append(True)
        #     info("Forcing raw embeddings loading for semantic context embedding disambiguations.")

    def get_all_preprocessed(self):
        res = super().get_all_preprocessed()
        res["undefined_element_index"] = self.undefined_element_index
        return res

    # endregion

    def save_raw_embedding_weights(self, weights):
        error("{} is for pretrained embeddings only.".format(self.name))


    def load_model_from_disk(self):
        """Load the component's model from disk"""
        csv_mapping_path = join(self.config.folders.raw_data, self.dir_name, self.base_name) + ".csv"
        self.read_raw_embedding_mapping(csv_mapping_path)
        self.model_loaded = True
        return True



    def read_raw_embedding_mapping(self, path):
        # check if there's a vocabulary file and map token to its position in the embedding list
        try:
            vocab_path = path + ".vocab"
            with open(vocab_path) as f:
                lines = [x.strip() for x in f.readlines()]
                for word in [x for x in lines if x]:
                    self.embedding_vocabulary_index[word] = len(self.embedding_vocabulary_index)
            info("Read {}-long embedding vocabulary from path {}".format(len(self.embedding_vocabulary_index), vocab_path))
            self.embeddings_path = path
            return
        except FileNotFoundError:
            pass

        # word - vector correspondence
        try:
            self.embeddings_source = pd.read_csv(path, sep=self.config.misc.csv_separator, header=None, index_col=0)
            info(f"Read embeddings of shape {self.embeddings_source.shape} using csv [separator]: [{self.config.misc.csv_separator}]")
        except ParserError as pe:
            error("Failed to read {}-delimited raw embedding from {}".format(self.config.misc.csv_separator, path), pe)
        except FileNotFoundError:
            error(f"Could not find embedding mapping file: {path}")
        # sanity check on defined dimension
        csv_dimension = self.embeddings_source.shape[-1]
        if self.dimension is not None and csv_dimension != self.dimension:
            error(f"Specified embedding dimension of {self.dimension} but read csv embeddings are {csv_dimension}-dimensional.")
        self.dimension = csv_dimension

    def __init__(self):
        Representation.__init__(self)

    # get vector representations of a list of words
    def get_embeddings(self, words):
        words = [w for w in words if w in self.embeddings_source.index]
        word_embeddings = self.embeddings_source.loc[words]
        # drop the nans and return
        return word_embeddings

    # for embeddings, vectors are already dense
    def get_dense_vector(self, vector):
        return vector

    # prepare embedding data to be ready for classification
    def aggregate_instance_vectors(self):
        """Method that maps features to a single vector per instance"""
        if self.aggregation == defs.alias.none:
            return
        if self.loaded_aggregated:
            debug("Skipping representation aggregation.")
            return
        aggr_str = self.aggregation
        if self.aggregation == defs.aggregation.pad: aggr_str += "_seq{}".format(self.sequence_length)
        info("Aggregating embeddings to single-vector-instances via the [{}] method.".format(aggr_str))
        # use words per document for the aggregation, aggregating function as an argument stats
        aggregation_stats = [0, 0]


        new_embedding_matrix = np.ndarray((0, self.dimension), np.float32)
        new_indices = []
        # map pre-aggregated to post-aggregated element indexes
        aggregated_index_mapping = {}
        encountered_indices = []



        for dset_idx in range(len(self.vector_indices)):
            aggregated_indices = []
            info("Aggregating embedding vectors for collection {}/{}".format(dset_idx + 1, len(self.vector_indices)))


            new_numel_per_instance = []
            curr_idx = 0
            for inst_idx, curr_instance in enumerate(self.vector_indices[dset_idx]):
                curr_instance = self.vector_indices[dset_idx][inst_idx]

                # average aggregation to a single vector
                if self.aggregation == "avg":
                    if tuple(curr_instance) in aggregated_index_mapping:
                        new_index = aggregated_index_mapping[curr_instance]
                        print()
                    else:
                        new_index = len(new_embedding_matrix)
                        new_vector = np.expand_dims(np.mean(self.embeddings_source[curr_instance], axis=0),axis=0)
                        # new_vector = np.mean(self.embedding[curr_instance], axis=0).reshape(1, self.dimension)
                        new_embedding_matrix = np.append(new_embedding_matrix, new_vector, axis=0)

                    new_numel_per_instance.append(1)
                    curr_instance = [new_index]
                # padding aggregation to specified vectors per instance
                elif self.aggregation == "pad":
                    num_vectors = len(curr_instance)
                    if self.sequence_length < num_vectors:
                        # truncate
                        curr_instance = curr_instance[:self.sequence_length]
                        aggregation_stats[0] += 1
                    elif self.sequence_length > num_vectors:
                        # make pad and stack vertically
                        pad_size = self.sequence_length - num_vectors
                        curr_instance += np.append(curr_instance, np.asarray([self.unknown_element_index] * pad_size))
                        aggregation_stats[1] += 1
                elif self.aggregation == defs.alias.none:
                    pass
                else:
                    error("Undefined aggregation: {}".format(self.aggregation))

                aggregated_indices.append(curr_instance)

                # curr_idx += inst_len
            # update the dataset vector collection and dimension
            self.vector_indices[dset_idx] = aggregated_indices
            # update the elements per instance
            self.elements_per_instance[dset_idx] = np.asarray(new_numel_per_instance, np.int32)

            # report stats
            if self.aggregation == "pad":
                info("Truncated {:.3f}% and padded {:.3f} % items.".format(*[x / len(self.vector_indices[dset_idx]) * 100 for x in aggregation_stats]))

        if new_embedding_matrix.size > 0:
            self.embeddings = new_embedding_matrix
        self.vector_indices, new_embedding_index = realign_embedding_index(self.vector_indices, np.arange(len(self.embeddings)))
        self.embeddings = self.embeddings[new_embedding_index]
        # flatten dataset vectors
        self.vector_indices = [np.concatenate(x) if x else np.ndarray((0,), np.int32) for x in self.vector_indices]
        info("Aggregated shapes, indices: {}, matrix: {}".format(shapes_list(self.vector_indices), self.embeddings.shape))

    # shortcut for reading configuration values
    def set_params(self):
        self.map_missing_unks = self.config.missing_words == "unk"
        Representation.set_params(self)
        self.compatible_aggregations = defs.aggregation.avail
        self.compatible_sequence_lengths = defs.sequence_length.avail

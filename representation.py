from os.path import join, basename
import pandas as pd
from pandas.errors import ParserError
from dataset import Dataset
from utils import error, tictoc, info, debug, read_pickled, write_pickled, warning, shapes_list, read_lines
import numpy as np
from serializable import Serializable
from semantic import SemanticResource

from bag import Bag, TFIDF


class Representation(Serializable):
    dir_name = "representation"

    @staticmethod
    def create(config):
        name = config.representation.name
        if name == Train.name:
            return Train(config)
        if name == BagRepresentation.name:
            return BagRepresentation(config)
        if name == TFIDFRepresentation.name:
            return TFIDFRepresentation(config)
        # any unknown name is assumed to be pretrained embeddings
        return VectorEmbedding(config)

    def __init__(self, can_fail_loading=True):
        self.set_params()
        Serializable.__init__(self, self.dir_name)
        # check for serialized mapped data
        self.set_serialization_params()
        # add paths for aggregated / transformed / enriched representations:
        self.set_representation_serialization_sources()
        # set required resources
        self.set_resources()
        # fetch the required data
        self.acquire_data(fatal_error=not can_fail_loading)

    # add exra representations-specific serialization paths
    def set_representation_serialization_sources(self):
        # compute names
        aggr = "".join(list(map(str, self.config.representation.aggregation + [self.sequence_length])))
        self.serialization_path_aggregated = "{}/{}.aggregated_{}.pickle".format(self.serialization_dir, self.name, aggr)
        extras = [self.serialization_path_aggregated, read_pickled]

        sem = SemanticResource.get_semantic_name(self.config)
        finalized_id = sem + "_" + self.config.semantic.enrichment if sem else "nosem"
        self.serialization_path_finalized = "{}/{}.aggregated_{}.finalized_{}.pickle".format(
            self.serialization_dir, self.name, aggr, finalized_id)
        extras = [self.serialization_path_aggregated, read_pickled]

        if self.config.has_transform():
            pass

        # fill in at the desired order (finalized, transformed, aggregated 
        self.data_paths = [self.serialization_path_finalized, self.serialization_path_aggregated] + self.data_paths
        self.read_functions = [read_pickled] * 2 + self.read_functions
        self.handler_functions = [self.handle_finalized, self.handle_aggregated] + self.handler_functions

    # shortcut for reading configuration values
    def set_params(self):
        self.representation_dim = self.config.representation.dimension
        self.dataset_name = self.config.dataset.name
        self.aggregation = self.config.representation.aggregation
        self.base_name = self.name
        self.sequence_length = self.config.representation.sequence_length
        self.dataset_name = Dataset.get_limited_name(self.config)
        self.map_missing_unks = self.config.representation.missing_words == "unk"
        if type(self.aggregation) == list:
            if self.aggregation[0] == "pad":
                self.vectors_per_doc = self.sequence_length
            elif self.aggregation[0] == "avg":
                self.vectors_per_doc = 1
            else:
                error("Undefined aggregation: {}".format(self.aggregation))
        else:
            error("Undefined aggregation: {}".format(self.aggregation))
        self.set_name()

    # name setter function, exists for potential overriding
    def set_name(self):
        self.name = "{}_{}_dim{}".format(self.base_name, self.dataset_name, self.representation_dim)

    # finalize embeddings to use for training, aggregating all data to a single ndarray
    # if semantic enrichment is selected, do the infusion
    def finalize(self, semantic, transform):
        if self.loaded_finalized:
            info("Skipping embeddings finalizing, since finalized data was already loaded.")
            return



        finalized_name = self.name
        if self.config.semantic.enrichment is not None:
            if self.config.representation.name == "train":
                error("Semantic enrichment undefined for embedding training, for now.")
            info("Enriching {} embeddings with semantic information, former having {} vecs/doc.".format(self.config.representation.name, self.vectors_per_doc))
            semantic_data = semantic.get_vectors()
            finalized_name += ".{}.enriched".format(SemanticResource.get_semantic_name(self.config))

            if self.config.semantic.enrichment == "concat":
                semantic_dim = len(semantic_data[0][0])
                self.final_dim = self.representation_dim + semantic_dim
                for dset_idx in range(len(semantic_data)):
                    info("Concatenating dataset part {}/{} to composite dimension: {}".format(dset_idx + 1, len(semantic_data), self.final_dim))
                    if self.vectors_per_doc > 1:
                        # tile the vector the needed times to the right, reshape to the correct dim
                        semantic_data[dset_idx] = np.reshape(np.tile(semantic_data[dset_idx], (1, self.vectors_per_doc)),
                                                             (-1, semantic_dim))
                    self.dataset_vectors[dset_idx] = np.concatenate(
                        [self.dataset_vectors[dset_idx], semantic_data[dset_idx]], axis=1)

            elif self.config.semantic.enrichment == "replace":
                self.final_dim = len(semantic_data[0][0])
                for dset_idx in range(len(semantic_data)):
                    info("Replacing dataset part {}/{} with semantic info of dimension: {}".format(dset_idx + 1, len(semantic_data), self.final_dim))
                    if self.vectors_per_doc > 1:
                        # tile the vector the needed times to the right, reshape to the correct dim
                        semantic_data[dset_idx] = np.reshape(np.tile(semantic_data[dset_idx], (1, self.vectors_per_doc)),
                                                             (-1, self.final_dim))
                    self.dataset_vectors[dset_idx] = semantic_data[dset_idx]
            else:
                error("Undefined semantic enrichment: {}".format(self.config.semantic.enrichment))
        else:
            info("Finalizing embeddings without semantic information.")
            finalized_name += ".finalized"
            dim = self.representation_dim if not self.config.representation.name == "train" else 1
            self.final_dim = dim
            # concatenating embeddings for each dataset portion into a single dataframe
            for dset_idx in range(len(self.dataset_vectors)):
                new_dset_embeddings = np.ndarray((0, dim), np.float32)
                for doc_idx in range(len(self.dataset_vectors[dset_idx])):
                    embeddings = self.dataset_vectors[dset_idx][doc_idx]
                    new_dset_embeddings = np.vstack([new_dset_embeddings, embeddings])
                self.dataset_vectors[dset_idx] = new_dset_embeddings

        # serialize finalized embeddings
        write_pickled(self.serialization_path_finalized, self.get_all_preprocessed())

    def handle_aggregated(self, data):
        self.handle_preprocessed(data)
        self.loaded_aggregated = True
        debug("Read aggregated dataset embeddings shapes: {}, {}".format(*shapes_list(self.dataset_vectors)))

    def handle_finalized(self, data):
        self.handle_preprocessed(data)
        self.loaded_finalized = True
        self.final_dim = data[0][0].shape[-1]
        debug("Read finalized dataset embeddings shapes: {}, {}".format(*shapes_list(self.dataset_vectors)))

    def get_zero_pad_element(self):
        return np.zeros((1, self.representation_dim), np.float32)

    def get_vocabulary_size(self):
        return len(self.dataset_words[0])

    def has_word(self, word):
        return word in self.embeddings.index

    def get_data(self):
        return self.dataset_vectors

    def get_final_dim(self):
        return self.final_dim

    def get_dim(self):
        return self.representation_dim

    # mark word-index relations for stat computation, add unk if needed
    def handle_raw_serialized(self, raw_serialized):
        # process as dataframe
        self.words_to_numeric_idx = {}
        self.embeddings = raw_serialized
        for w in self.embeddings.index.tolist():
            self.words_to_numeric_idx[w] = len(self.words_to_numeric_idx)

    # mark preprocessing
    def handle_preprocessed(self, preprocessed):
        self.dataset_vectors, self.elements_per_instance, self.missing, \
            self.undefined_word_index, self.present_word_indexes = preprocessed
        self.loaded_preprocessed = True
        debug("Read preprocessed dataset embeddings shapes: {}, {}".format(*list(map(len, self.dataset_vectors))))

    def handle_raw(self, raw_data):
        pass

    def fetch_raw(self, path):
        # assume embeddings are dataframes
        return None

    def preprocess(self):
        pass

    def loaded_enriched(self):
        return self.loaded_finalized

    def get_present_word_indexes(self):
        return self.present_word_indexes

    def set_transformed(self, transform):
        """Update representation information as per the input transform"""
        self.name += transform.get_name()
        self.dataset_vectors = transform.get_vectors()
        self.representation_dim = transform.get_dimension()

    def get_vectors(self):
        return self.dataset_vectors


class Embedding(Representation):
    name = ""
    words = []
    dataset_vectors = None
    embeddings = None
    words_to_numeric_idx = None
    missing = []
    representation_dim = None
    sequence_length = None

    def save_raw_embedding_weights(self, weights):
        error("{} is for pretrained embeddings only.".format(self.name))

    def set_resources(self):
        csv_mapping_name = "{}/{}.csv".format(self.raw_data_dir, self.base_name)
        self.resource_paths.append(csv_mapping_name)
        self.resource_read_functions.append(self.read_raw_embedding_mapping)
        self.resource_handler_functions.append(lambda x: x)

        # need the raw embeddings even if processed embedding data is available
        if self.config.has_semantic() and self.config.semantic.name == "context":
            # need the raw embeddings even if processed embedding data is available
            self.resource_always_load_flag.append(True)
            info("Forcing raw embeddings loading for semantic context embedding disambiguations.")

    def read_raw_embedding_mapping(self, path):
        # word - vector correspondence
        try:
            self.embeddings = pd.read_csv(path, delimiter=" ", header=None, index_col=0)
        except ParserError as pe:
            error(pe.msg)

    def __init__(self):
        Representation.__init__(self, can_fail_loading=False)

    # get vector representations of a list of words
    def get_embeddings(self, words):
        words = [w for w in words if w in self.embeddings.index]
        word_embeddings = self.embeddings.loc[words]
        # drop the nans and return
        return word_embeddings

    # for embeddings, vectors are already dense
    def get_dense_vector(self, vector):
        return vector

    # compute dense elements
    def compute_dense(self):
        # instance vectors are already dense - just make dataset-level ndarrays
        for dset_idx in range(len(self.dataset_vectors)):
            self.dataset_vectors[dset_idx] = pd.concat(self.dataset_vectors[dset_idx]).values

    # prepare embedding data to be ready for classification
    def aggregate_instance_vectors(self):
        """Method that maps features to a single vector per instance"""
        if self.loaded_aggregated or self.loaded_finalized:
            return
        info("Aggregating embeddings to single-vector-instances via the {} method.".format(self.aggregation))
        # use words per document for the aggregation, aggregating function as an argument
        aggregated_dataset_vectors = np.ndarray((0, self.representation_dim), np.float32)
        # stats
        aggregation_stats = 0, 0

        for dset_idx in range(len(self.dataset_vectors)):
            info("Aggregating embedding vectors for {}-sized collection {}/{}".format(
                len(self.dataset_vectors[dset_idx]), dset_idx + 1, len(self.dataset_vectors)))

            curr_idx = 0
            for inst_len in self.elements_per_instance[dset_idx]:
                curr_instance = self.dataset_vectors[dset_idx][curr_idx: curr_idx + inst_len]

                # average aggregation to a single vector
                if self.aggregation[0] == "avg":
                    curr_instance = np.mean(curr_instance, axis=0).reshape(1, self.representation_dim)
                # padding aggregation to specified vectors per instance
                elif self.aggregation[0] == "pad":
                    # filt = self.aggregation[1]
                    num_vectors = len(curr_instance)
                    if self.sequence_length < num_vectors:
                        # truncate
                        curr_instance = curr_instance[:self.sequence_length, :]
                        aggregation_stats[0] += 1
                    elif self.sequence_length > num_vectors:
                        # make pad and stack vertically
                        pad_size = self.sequence_length - num_vectors
                        pad = np.tile(self.get_zero_pad_element(), (pad_size, 1), np.float32)
                        curr_instance = np.append(curr_instance, pad, axis=0)
                        aggregation_stats[1] += 1
                else:
                    error("Undefined aggregation: {}".format(self.aggregation))

                aggregated_dataset_vectors = np.append(aggregated_dataset_vectors, curr_instance, axis=0)
                curr_idx += inst_len
            # update the dataset vector collection
            self.dataset_vectors[dset_idx] = aggregated_dataset_vectors
            # report stats
            if self.aggregation[0] == "pad":
                info("Truncated {:.3f}% and padded {:.3f} % items.".format(*[x / len(self.dataset_vectors[dset_idx]) * 100 for x in aggregation_stats]))



# generic class to load pickled embedding vectors
class VectorEmbedding(Embedding):
    name = "vector"
    unknown_word_token = "unk"

    # expected raw data path
    def get_raw_path(self):
        return "{}/{}_dim{}.pickle".format(self.raw_data_dir, self.base_name, self.representation_dim)

    # transform input texts to embeddings
    def map_text(self, dset):
        if self.loaded_preprocessed or self.loaded_aggregated or self.loaded_finalized:
            return
        info("Mapping dataset: {} to {} embeddings.".format(dset.name, self.name))
        text_bundles = dset.train, dset.test
        self.dataset_vectors = []
        self.present_word_indexes = []
        self.vocabulary = dset.vocabulary
        self.elements_per_instance = []

        if self.unknown_word_token not in self.embeddings and self.map_missing_unks:
            warning("[{}] unknown token missing from embeddings, adding it as zero vector.".format(self.unknown_word_token))
            self.embeddings.loc[self.unknown_word_token] = np.zeros(self.representation_dim)

        # loop over input text bundles (e.g. train & test)
        for dset_idx in range(len(text_bundles)):
            self.dataset_vectors.append([])
            self.present_word_indexes.append([])
            self.elements_per_instance.append([])
            with tictoc("Embedding mapping for text bundle {}/{}".format(dset_idx + 1, len(text_bundles))):
                info("Mapping text bundle {}/{}: {} texts".format(dset_idx + 1, len(text_bundles), len(text_bundles[dset_idx])))
                hist = {w: 0 for w in self.embeddings.index}
                hist_missing = {}
                for j, doc_wp_list in enumerate(text_bundles[dset_idx]):
                    # drop POS
                    word_list = [wp[0] for wp in doc_wp_list]
                    debug("Text {}/{} with {} words".format(j + 1, len(text_bundles[dset_idx]), len(word_list)))
                    # check present & missing words
                    missing_words, missing_index, present_words, present_index = [], [], [], []
                    for w, word in enumerate(word_list):
                        if word not in self.embeddings.index:
                            # debug("Word [{}] not in embedding index.".format(word))
                            missing_words.append(word)
                            missing_index.append(w)
                            if word not in hist_missing:
                                hist_missing[word] = 0
                            hist_missing[word] += 1
                        else:
                            present_words.append(word)
                            present_index.append(w)
                            hist[word] += 1

                    # handle missing
                    if not self.map_missing_unks:
                        # ignore & discard missing words
                        word_list = present_words
                    else:
                        # replace missing words with UNKs
                        for m in missing_index:
                            word_list[m] = self.unknown_word_token

                    if not present_words and not self.map_missing_unks:
                        # no words present in the mapping, force
                        error("No words persent in document.")

                    # get embeddings
                    text_embeddings = self.embeddings.loc[word_list]
                    if len(text_embeddings) == 0:
                        import pdb; pdb.set_trace()
                    self.dataset_vectors[-1].append(text_embeddings)

                    # update present words and their index, per doc
                    self.elements_per_instance[-1].append(len(text_embeddings))
                    self.present_word_indexes[-1].append(present_index)

            self.print_word_stats(hist, hist_missing)

        # write
        info("Writing embedding mapping to {}".format(self.serialization_path_preprocessed))
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed())

    def print_word_stats(self, hist, hist_missing):
        num_words_hit, num_hit = sum([1 for v in hist if hist[v] > 0]), sum(hist.values())
        num_words_miss, num_miss = len(hist_missing.keys()), sum(hist_missing.values())
        num_total = sum(list(hist.values()) + list(hist_missing.values()))

        debug("Found {} instances or {:.3f} % of total {}, for {} words.".format(num_hit, num_hit / num_total * 100, num_total, num_words_hit))
        debug("Missed {} instances or {:.3f} % of total {}, for {} words.".format(num_miss, num_miss / num_total * 100, num_total, num_words_miss))
        if num_hit == 0:
            error("No hits in embedding generation.")

    def __init__(self, config):
        self.config = config
        self.name = self.base_name = self.config.representation.name
        Embedding.__init__(self)

    def get_all_preprocessed(self):
        return [self.dataset_vectors, self.elements_per_instance, self.missing, None, self.present_word_indexes]


class Train(Representation):
    name = "train"
    undefined_word_name = "unk"

    def __init__(self, config):
        self.config = config
        Representation.__init__(self, can_fail_loading=True)

    # embedding training data (e.g. word indexes) does not depend on embedding dimension
    # so naming is overriden to omit embedding dimension
    def set_name(self):
        self.name = "{}_{}".format(self.base_name, self.dataset_name)

    # transform input texts to embeddings
    def map_text(self, dset):
        # assign all embeddings
        self.embeddings = pd.DataFrame(dset.vocabulary_index, dset.vocabulary)
        if self.loaded_preprocessed or self.loaded_aggregated or self.loaded_finalized:
            return
        info("Mapping {} to {} represntations.".format(dset.name, self.name))
        text_bundles = dset.train, dset.test
        self.dataset_vectors = []
        self.undefined_word_index = dset.undefined_word_index
        non_train_words = []
        # loop over input text bundles (e.g. train & test)
        for i in range(len(text_bundles)):
            self.dataset_vectors.append([])
            with tictoc("Embedding mapping for text bundle {}/{}".format(i + 1, len(text_bundles))):
                info("Mapping text bundle {}/{}: {} texts".format(i + 1, len(text_bundles), len(text_bundles[i])))
                for j in range(len(text_bundles[i])):
                    word_list = [word_pos[0] for word_pos in text_bundles[i][j]]
                    index_list = [[dset.word_to_index[w]] if w in dset.vocabulary else [dset.undefined_word_index] for w in word_list]
                    embedding = pd.DataFrame(index_list, index=word_list)
                    debug("Text {}/{}".format(j + 1, len(text_bundles[i])))
                    self.dataset_vectors[-1].append(embedding)
                    # get test words, perhaps
                    if i > 0:
                        for w in word_list:
                            if w not in non_train_words:
                                non_train_words.append(w)
        self.dataset_words = [dset.vocabulary, non_train_words]
        # write mapped data
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed())

    def get_all_preprocessed(self):
        return [self.dataset_vectors, self.dataset_words, None, self.undefined_word_index, None]

    def get_zero_pad_element(self):
        return self.undefined_word_index

    def get_raw_path(self):
        return None

    def fetch_raw(self, dummy_input):
        return dummy_input

    def handle_preprocessed(self, preprocessed):
        self.loaded_preprocessed = True
        self.dataset_vectors, self.dataset_words, self.missing, self.undefined_word_index, _ = preprocessed

    def save_raw_embedding_weights(self, weights, write_dir):
        # rename to generic vectorembedding
        emb_name = ("raw_" + self.name + "_dim{}.pickle".
                    format(self.config.representation.dimension)).\
            replace(Train.name, VectorEmbedding.name)
        writepath = join(write_dir, emb_name)
        # associate with respective words
        index = self.dataset_words[0] + [self.undefined_word_name]
        data = pd.DataFrame(weights, index=index)
        write_pickled(writepath, data)

    def get_words(self):
        return self.dataset_words


class BagRepresentation(Representation):
    name = "bag"
    bag_class = TFIDF
    token_list = None

    def __init__(self, config):
        self.config = config
        self.config.representation.dimension = None
        Representation.__init__(self)

    def set_name(self):
        # disable the dimension
        Representation.set_name(self)
        # if external token list, add its length
        if self.config.representation.token_list is not None:
            self.name += "_tok_{}".format(basename(self.config.representation.token_list))

    def set_resources(self):
        if self.config.representation.token_list is not None:
            self.resource_paths.append(self.config.representation.token_list)
            self.resource_read_functions.append(read_lines)
            self.resource_handler_functions.append(self.handle_token_list)
            self.resource_always_load_flag.append(False)

    def handle_token_list(self, tok_list):
        info("Using external, {}-length token list.".format(len(tok_list)))
        self.token_list = tok_list

    def get_raw_path(self):
        return None

    def get_all_preprocessed(self):
        return [self.dataset_vectors, self.elements_per_instance, None, None, self.present_word_indexes]

    # for bags, existing vector is a sparse dict list. Fill with zeros.
    def get_dense_vector(self, doc_dict):
        full_vector = np.zeros((self.representation_dim,), np.float32)
        for t in doc_dict:
            full_vector[t] = doc_dict[t]
        return full_vector

    # sparse to dense
    def compute_dense(self):
        for dset_idx in range(len(self.dataset_vectors)):
            for vec_idx in range(len(self.dataset_vectors[dset_idx])):
                self.dataset_vectors[dset_idx][vec_idx] = self.get_dense_vector(self.dataset_vectors[dset_idx][vec_idx])
            # to ndarray
            self.dataset_vectors[dset_idx] = np.array(self.dataset_vectors[dset_idx])

    def aggregate_instance_vectors(self):
        # bag representations produce ready-to-use vectors
        pass

    def map_text(self, dset):
        if self.token_list is None:
            self.token_list = dset.vocabulary
            self.representation_dim = len(self.token_list)
        if self.loaded_preprocessed or self.loaded_aggregated or self.loaded_finalized:
            return
        info("Mapping {} to {} representation.".format(dset.name, self.name))

        self.dataset_words = [self.token_list, None]
        self.dataset_vectors = []
        self.present_word_indexes = []

        # train
        self.bag = self.bag_class()
        self.bag.set_token_list(self.token_list)
        self.bag.map_collection(dset.train)
        self.dataset_vectors.append(self.bag.get_weights())
        self.present_word_indexes.append(self.bag.get_present_token_indexes())

        # set representation dim and update name
        self.representation_dim = len(self.token_list)
        self.set_name()

        # test
        self.bag = self.bag_class()
        self.bag.set_token_list(self.token_list)
        self.bag.map_collection(dset.test)
        self.dataset_vectors.append(self.bag.get_weights())
        self.present_word_indexes.append(self.bag.get_present_token_indexes())

        # write mapped data
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed())


class TFIDFRepresentation(BagRepresentation):
    name = "tfidf"
    bag_class = TFIDF

    def __init__(self, config):
        BagRepresentation.__init__(self, config)

    # nothing to load, can be computed on the fly
    def fetch_raw(self, path):
        pass

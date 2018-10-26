import os
import logging
import pandas as pd
import csv
import pickle
from utils import error, tic, toc, info, debug
import numpy as np


class Embedding():
    name = ""
    serialization_dir = "serializations/embeddings"
    words = []
    dataset_embeddings = None
    embeddings = None
    words_to_numeric_idx = None
    missing = []
    loaded_mapped_embeddings = False
    embedding_dim = None
    sequence_length = None

    def __init__(self, params):
        self.embedding_dim = int(params[0])

    def get_zero_pad_element(self):
        return np.ndarray((1, self.embedding_dim), np.float32)

    def get_vocabulary_size(self):
        return len(self.words[0])

    def get_embeddings(self, words):
        word_embeddings = self.embeddings.loc[words].dropna()
        # drop the nans and return
        return word_embeddings

    def has_word(self, word):
        return word in self.embeddings.index

    def make(self, config):
        self.config = config
        self.dataset_name = self.config.get_dataset()
        aggr = self.config.get_aggregation().split(",")
        self.aggregation, self.aggregation_params = aggr[0], aggr[1:]
        if self.config.option("data_limit"):
            self.dataset_name += "_limit{}".format(self.config.option("data_limit"))
        self.serialization_dir = os.path.join(self.config.get_serialization_dir(), "embeddings")
        if not os.path.exists(self.serialization_dir):
            os.makedirs(self.serialization_dir, exist_ok=True)

        self.mapped_data_serialization_path = os.path.join(self.serialization_dir, "embeddings_mapped_{}_{}.pickle".format(
            self.dataset_name, self.name))



    def read_pickled(self, pickled_path):
        # pickle them
        info("Reading pickled embedding data from {}".format(pickled_path))
        with open(pickled_path, "rb") as f:
            data = pickle.load(f)
            self.embeddings, self.words, self.words_to_numeric_idx = data[0], data[1]

    def write_pickled(self, pickled_path, data):
        tic()
        info("Pickling embedding data to {}".format(pickled_path))
        with open(pickled_path, "wb") as f:
            pickle.dump(data , f)
        toc("Pickling")

    def get_words(self):
        return self.words

    def get_data(self):
        return self.dataset_embeddings

    # collect the words per dataset and document
    def collect_dataset_words(self):
        for dset in self.dataset_embeddings:
            self.words.append([])
            for document in dset:
                doc_words = document.index.tolist()
                self.words[-1].append(doc_words)

    # prepare embedding data to be ready for classification
    def prepare(self):
        info("Preparing embeddings.")
        if not self.loaded_mapped_embeddings:
            # save the words
            info("Storing the embedding word list.")
            self.collect_dataset_words()

        aggr = self.config.get_aggregation().split(",")
        aggregation = aggr[0]
        params = aggr[1:] if len(aggr) > 1 else []
        info("Aggregating embeddings via the {} method.".format(aggregation))
        if aggregation == "avg":
            self.vectors_per_document = 1
            # average all word vectors in the doc
            for dset_idx in range(len(self.dataset_embeddings)):
                aggregated_doc_vectors = []
                for doc_dict in self.dataset_embeddings[dset_idx]:
                    aggregated_doc_vectors.append(np.mean(doc_dict.values, axis=0))
                self.dataset_embeddings[dset_idx] = np.concatenate(aggregated_doc_vectors).reshape(len(aggregated_doc_vectors), self.embedding_dim)
        elif aggregation == "pad":
            if not params:
                error("Parameters required for pad aggregation.")
            num, filter = int(params[0]), params[1]
            info("Aggregation with pad: {} {}".format(num, filter))
            self.vectors_per_document = num
            zero_pad = self.get_zero_pad_element()

            for dset_idx in range(len(self.dataset_embeddings)):
                for doc_idx in range(len(self.dataset_embeddings[dset_idx])):
                    if len(self.dataset_embeddings[dset_idx][doc_idx]) > num:
                        # prune
                        self.dataset_embeddings[dset_idx][doc_idx] = self.dataset_embeddings[dset_idx][doc_idx][:num]
                    elif len(self.dataset_embeddings[dset_idx][doc_idx]) < num:
                        # pad
                        num_to_pad = num - len(self.dataset_embeddings[dset_idx][doc_idx])
                        pad = pd.DataFrame(np.tile(zero_pad, (num_to_pad, 1)), index= ['N' for _ in range(num_to_pad)])
                        self.dataset_embeddings[dset_idx][doc_idx] = pd.concat([self.dataset_embeddings[dset_idx][doc_idx], pad])


    # finalize embeddings to use for training, aggregating all data to a single ndarray
    # if semantic enrichment is selected, do the infusion
    def finalize(self, semantic_data):
        do_enrichment = semantic_data is not None
        if do_enrichment:
            info("Enriching embeddings with semantic information.")
            if self.name == "train":
                error("Semantic enrichment undefined for embedding training, for now.")

        if self.config.get_enrichment() == "concat":
            composite_dim = self.embedding_dim + len(semantic_data[0][0])
            for dset_idx in range(len(semantic_data)):
                info("Concatenating dataset part {}/{} to composite dimension: {}".format(dset_idx+1, len(semantic_data), composite_dim))
                new_dset_embeddings = np.ndarray((0, composite_dim), np.float32)
                for doc_idx in range(len(semantic_data[dset_idx])):
                    # debug("Enriching document {}/{}".format(doc_idx+1, len(semantic_data[dset_idx])))
                    embeddings = self.dataset_embeddings[dset_idx][doc_idx]
                    sem_vectors = np.asarray(semantic_data[dset_idx][doc_idx], np.float32)
                    if embeddings.ndim > 1:
                        # tile sem. vectors
                        sem_vectors = np.tile(sem_vectors, (len(embeddings), 1))
                        new_dset_embeddings = np.vstack([new_dset_embeddings, np.concatenate([embeddings, sem_vectors], axis=1)])
                    else:
                        new_dset_embeddings = np.vstack([new_dset_embeddings, np.concatenate([embeddings, sem_vectors])])
                self.dataset_embeddings[dset_idx] = new_dset_embeddings

        else:
            info("Finalizing embeddings.")
            for dset_idx in range(len(self.dataset_embeddings)):
                dim = self.embedding_dim if not self.name == "train" else 1
                new_dset_embeddings = np.ndarray((0, dim), np.float32)
                for doc_idx in range(len(self.dataset_embeddings[dset_idx])):
                    # debug("Enriching document {}/{}".format(doc_idx+1, len(semantic_data[dset_idx])))
                    embeddings = self.dataset_embeddings[dset_idx][doc_idx]
                    new_dset_embeddings = np.vstack([new_dset_embeddings, embeddings])
                self.dataset_embeddings[dset_idx] = new_dset_embeddings


    def get_dim(self):
        return self.embedding_dim

class Glove(Embedding):
    name = "glove"
    dataset_name = ""

    def load_raw_embeddings(self):
        raw_data_path = os.path.join("embeddings/glove.6B.{}d.txt".format(self.embedding_dim))
        pickled_raw_path = os.path.join(self.serialization_dir, "glove6B.{}d.pickle".format(self.embedding_dim))

        # read pickled existing data
        if os.path.exists(pickled_raw_path):
            self.read_pickled(pickled_raw_path)
            return

        # load existing raw data
        if os.path.exists(raw_data_path):
            info("Reading raw embedding data from {}".format(raw_data_path))
            tic()
            self.embeddings = pd.read_csv(raw_data_path, index_col = 0, header=None, sep=" ", quoting=csv.QUOTE_NONE)
            self.words_to_numeric_idx = {word:i for word,i in zip(self.embeddings.index.values.tolist(), range(len(self.embeddings.index))) }
            toc("Reading raw data")
            return
        # else, gotta download the raw data
        error("Downloaded glove embeddings missing from {}. Get them from https://nlp.stanford.edu/projects/glove/".format(raw_data_path))


    def make(self, config):
        Embedding.make(self, config)


        if os.path.exists(self.mapped_data_serialization_path):
            info("Reading existing mapped embedding data from {}".format(self.mapped_data_serialization_path))
            with open(self.mapped_data_serialization_path, "rb") as f:
                [self.dataset_embeddings, self.words, self.missing_words] = pickle.load(f)
                self.collect_dataset_words()
            self.loaded_mapped_embeddings = True

        self.load_raw_embeddings()


    # transform input texts to embeddings
    def map_text(self, dset):

        if self.loaded_mapped_embeddings:
            return
        info("Mapping {} to {} embeddings.".format(dset.name, self.name))
        text_bundles = dset.train, dset.test
        self.dataset_embeddings = []
        # loop over input text bundles (e.g. train & test)
        for i in range(len(text_bundles)):
            self.dataset_embeddings.append([])
            tic()
            info("Mapping text bundle {}/{}: {} texts".format(i+1, len(text_bundles), len(text_bundles[i])))
            hist = {w: 0 for w in self.words_to_numeric_idx}
            hist_missing = {}
            for j in range(len(text_bundles[i])):
                word_list = text_bundles[i][j]
                debug("Text {}/{}".format(j+1, len(text_bundles[i])))
                text_embeddings = self.embeddings.loc[word_list]

                # stats
                missing_words = text_embeddings[text_embeddings.isnull().any(axis=1)].index.tolist()
                text_embeddings = self.embeddings.loc[word_list].dropna()
                present_words = text_embeddings.index.tolist()
                for w in present_words:
                    hist[w] += 1
                for m in missing_words:
                    if m not in hist_missing:
                        hist_missing[m] = 0
                    hist_missing[m] += 1

                self.dataset_embeddings[-1].append(text_embeddings)

            toc("Embedding mapping for text bundle {}/{}".format(i+1, len(text_bundles)))

            num_words_hit, num_hit = sum([1 for v in hist if hist[v] > 0]), sum(hist.values())
            num_words_miss, num_miss = len(hist_missing.keys()), sum(hist_missing.values())
            num_total = sum(list(hist.values()) + list(hist_missing.values()))

            debug("Found {} instances or {:.3f} % of total {}, for {} words.".format(num_hit, num_hit/num_total*100, num_total, num_words_hit))
            debug("Missed {} instances or {:.3f} % of total {}, for {} words.".format(num_miss, num_miss/num_total*100, num_total, num_words_miss))
            self.missing.append(hist_missing)

        # write
        info("Writing embedding mapping to {}".format(self.mapped_data_serialization_path))
        self.write_pickled(self.mapped_data_serialization_path, [self.dataset_embeddings, self.words, self.missing])
        # log missing words
        for d in range(len(self.missing)):
            l = ['train', 'text']
            missing_filename = os.path.join(self.serialization_dir, "missing_words_{}_{}_{}.txt".format(self.name, dset.name, l[d]))
            info("Writing missing words to {}".format(missing_filename))
            with open(missing_filename, "w") as f:
                f.write("\n".join(self.missing[d].keys()))

    def __init__(self, params):
        Embedding.__init__(self, params)


class Train(Embedding):
    name = "train"

    def __init__(self, params):
        Embedding.__init__(self, params)
        self.sequence_length = int(params[1])

    def make(self, config):
        self.config = config
        Embedding.make(self, config)
        if self.aggregation not in ["pad"]:
            error("Aggregation {} incompatible with {} embedding".format(self.aggregation, self.name))
        self.dataset_name = self.config.get_dataset()
        if self.config.option("data_limit"):
            self.dataset_name += "_limit{}".format(self.config.option("data_limit"))
        self.serialization_dir = os.path.join(self.config.get_serialization_dir(), "embeddings")

        if not os.path.exists(self.serialization_dir):
            os.makedirs(self.serialization_dir, exist_ok=True)

        if os.path.exists(self.mapped_data_serialization_path):
            info("Reading existing mapped embedding data from {}".format(self.mapped_data_serialization_path))
            with open(self.mapped_data_serialization_path, "rb") as f:
                [self.dataset_embeddings, self.words, self.missing_words, self.undefined_word_index] = pickle.load(f)
            self.loaded_mapped_embeddings = True

    # transform input texts to embeddings
    def map_text(self, dset):

        # assign all embeddings
        self.embeddings = pd.DataFrame(dset.vocabulary_index, dset.vocabulary)

        if self.loaded_mapped_embeddings:
            return
        info("Mapping {} to {} embeddings.".format(dset.name, self.name))
        text_bundles = dset.train, dset.test
        self.dataset_embeddings = []
        self.undefined_word_index = dset.undefined_word_index
        non_train_words = []
        # loop over input text bundles (e.g. train & test)
        for i in range(len(text_bundles)):
            self.dataset_embeddings.append([])
            tic()
            info("Mapping text bundle {}/{}: {} texts".format(i+1, len(text_bundles), len(text_bundles[i])))
            for j in range(len(text_bundles[i])):
                word_list = text_bundles[i][j]
                index_list = [ [dset.word_to_index[w]] if w in dset.vocabulary else [dset.undefined_word_index] for w in word_list]
                print(index_list)
                embedding = pd.DataFrame(index_list, index = word_list)
                debug("Text {}/{}".format(j+1, len(text_bundles[i])))
                self.dataset_embeddings[-1].append(embedding)
                if i > 0:
                    for w in word_list:
                        if w not in non_train_words:
                            non_train_words.append(w)
                    # get test words, perhaps



            toc("Embedding mapping for text bundle {}/{}".format(i+1, len(text_bundles)))
        self.words = [dset.vocabulary, non_train_words]

        # write mapped data
        info("Writing embedding mapping to {}".format(self.mapped_data_serialization_path))
        
        self.write_pickled(self.mapped_data_serialization_path, [self.dataset_embeddings, self.words, None, self.undefined_word_index])

    def get_zero_pad_element(self):
        return self.undefined_word_index




class Universal_sentence_encoder:
    pass

class ELMo:
    # https://allennlp.org/elmo
    pass

class Word2vec(Embedding):
    name = "word2vec"
    def __init__(self, params):
        Embedding.__init__(self, params)

class FastText:
    # https://fasttext.cc/docs/en/english-vectors.html
    pass
class Doc2vec:
    pass

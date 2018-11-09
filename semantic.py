from os.path import join, exists
from dataset import Dataset
import pickle
from utils import tictoc, error, info, debug, warning, write_pickled, read_pickled
import numpy as np
from serializable import Serializable
from nltk.corpus import wordnet as wn
import json
import urllib


class SemanticResource(Serializable):
    dir_name = "semantic"
    semantic_name = None
    name = None
    do_spread_activation = False
    loaded_vectorized = False

    def __init__(self, config):
        Serializable.__init__(self, self.dir_name)

    def create(config):
        name = config.semantic.name
        if name == Wordnet.name:
            return Wordnet(config)
        else:
            error("Undefined semantic resource: {}".format(name))
    pass

    def get_semantic_name(config):
        if not config.has_semantic():
            return None
        freq_filtering = "ALL" if not config.semantic.threshold else "fthres{}".format(config.semantic.threshold)
        sem_weights = "w{}".format(config.semantic.weights)
        disambig = "disam{}".format(config.semantic.disambiguation)
        semantic_name = "{}_{}_{}_{}".format(config.semantic.name, sem_weights, freq_filtering, disambig)
        return semantic_name

    def handle_vectorized(self, data):
        self.semantic_document_vectors, self.synset_order = data
        self.loaded_preprocessed = True
        self.loaded_vectorized = True


    def lookup(self, candidate):
        error("Attempted to lookup from the base class")

class Wordnet(SemanticResource):

    word_synset_lookup_cache = {}
    word_synset_embedding_cache = {}

    synset_freqs = []
    synset_tfidf_freqs = []
    dataset_freqs = []
    dataset_minmax_freqs = []
    assignments = {}
    synset_context_word_threshold = None
    name = "wordnet"

    reference_synsets = None

    pos_tag_mapping = {}

    def __init__(self, config):
        self.config = config
        self.base_name = self.name
        SemanticResource.__init__(self, config)

        # map nltk pos maps into meaningful wordnet ones
        self.pos_tag_mapping = {"VB": wn.VERB, "NN": wn.NOUN, "JJ": wn.ADJ, "RB": wn.ADV}

        self.semantic_freq_threshold = config.semantic.threshold
        self.semantic_weights = config.semantic.weights
        self.semantic_unit = config.semantic.unit
        self.disambiguation = config.semantic.disambiguation.lower()
        if config.semantic.spreading_activation:
            self.do_spread_activation = True
            self.spread_steps, self.spread_decay = config.semantic.spreading_activation[0], \
                                                   config.semantic.spreading_activation[1]

        self.dataset_name = Dataset.get_limited_name(self.config)
        self.semantic_name = SemanticResource.get_semantic_name(self.config)
        self.name = "{}_{}".format(self.dataset_name, self.semantic_name)
        if self.do_spread_activation:
            self.name += "spread{}dec{}".format(self.spread_steps, self.spread_decay)

        if self.disambiguation == "context-embedding":
            self.semantic_embedding_dim = self.config.embedding.dimension
            # incompatible with embedding training
            if config.embedding.name == "train":
                error("Embedding train mode incompatible with semantic embeddings.")
            # preload the concept list
            context_file = config.semantic.context_file
            with open(context_file, "rb") as f:
                data = pickle.load(f)
                self.semantic_context = {}
                if self.synset_context_word_threshold is not None:
                    info("Limiting reference context synsets to a frequency threshold of {}".format(self.synset_context_word_threshold))
                    for s, wl in data.items():
                        if len(wl) < self.synset_context_word_threshold:
                            continue
                        self.semantic_context[s] = wl
                else:
                    self.semantic_context = data;
                self.reference_synsets = list(self.semantic_context.keys())
                if not self.reference_synsets:
                    error("Frequency threshold of synset context: {} resulted in zero reference synsets".format(self.synset_context_word_threshold))
                info("Applying {} reference synsets from pre-extracted synset words".format(len(self.reference_synsets)))
            # calculate the synset embeddings path
            thr = ""
            if self.synset_context_word_threshold:
                thr += "_thresh{}".format(self.synset_context_word_threshold)
            self.sem_embeddings_path = join(self.serialization_dir, "semantic_embeddings_{}_{}{}{}".format(self.name, self.embedding.name, self.semantic_embedding_dim, thr))
            self.semantic_embedding_aggr = config.semantic.context_aggregation

        # setup serialization paramas
        self.set_serialization_params()
        # add extras
        self.serialization_path_vectorized = self.serialization_path_preprocessed + ".vectorized"
        self.data_paths.insert(0, self.serialization_path_vectorized)
        self.read_functions.insert(0, read_pickled)
        self.handler_functions.insert(0, self.handle_vectorized)

        # load if existing
        self.acquire2(fatal_error=False)


    def fetch_raw(self, dummy_input):
        # set name and paths to load sem. emb.
        # or set a sem. emb. serializable class
        if self.disambiguation == "context-embedding":
            self.compute_semantic_embeddings()

    def handle_preprocessed(self, preprocessed):
        self.loaded_preprocessed = True
        self.assignments, self.synset_freqs, self.dataset_freqs, self.synset_tfidf_freqs, self.reference_synsets = preprocessed

    def handle_raw_serialized(self, raw_serialized):
        pass
    def handle_raw(self, raw_data):
        pass

    # prune semantic information units wrt a frequency threshold
    def apply_freq_filtering(self, freq_dict_list, dataset_freqs, force_reference=False):
        info("Applying synset frequency filtering with a threshold of {}".format(self.semantic_freq_threshold))
        with tictoc("Dataset-level frequency filtering"):
            # delete from dataset-level dicts
            synsets_to_delete = set()
            for synset in dataset_freqs:
                if dataset_freqs[synset] < self.semantic_freq_threshold:
                    synsets_to_delete.add(synset)
        # if forcing reference, we can override the freq threshold for these synsets
        if force_reference:
            orig_num = len(synsets_to_delete)
            synsets_to_delete = [x for x in synsets_to_delete if x not in self.reference_synsets]
            info("Limiting synsets-to-delete from {} to {} due to forcing to reference synset set".format(orig_num, len(synsets_to_delete)))
        if not synsets_to_delete:
            return  freq_dict_list, dataset_freqs
        info("Will remove {}/{} synsets due to a freq threshold of {}".format(len(synsets_to_delete), len(dataset_freqs), self.semantic_freq_threshold))
        with tictoc("Document-level frequency filtering"):
            # delete
            for synset in synsets_to_delete:
                del dataset_freqs[synset]
                for doc_dict in freq_dict_list:
                    if synset in doc_dict:
                        del doc_dict[synset]
        info("Synset frequency filtering resulted in {} synsets.".format(len(dataset_freqs)))
        return  freq_dict_list, dataset_freqs

    # merge list of document-wise frequency dicts
    # to a single, dataset-wise frequency dict
    def doc_to_dset_freqs(self, freq_dict_list, force_reference = False):
        dataset_freqs = {}
        for doc_dict in freq_dict_list:
            for synset, freq in doc_dict.items():
                if synset not in dataset_freqs:
                    dataset_freqs[synset] = 0
                dataset_freqs[synset] += freq
        # frequency filtering, if defined
        if self.semantic_freq_threshold:
            freq_dict_list, dataset_freqs = self.apply_freq_filtering(freq_dict_list, dataset_freqs, force_reference)

        # complete document-level freqs with zeros for dataset-level synsets missing in the document level
        #for d, doc_dict in enumerate(freq_dict_list):
        #    for synset in [s for s in dataset_freqs if s not in doc_dict]:
        #        freq_dict_list[d][synset] = 0
        return dataset_freqs, freq_dict_list


    # tf-idf computation
    def compute_tfidf_weights(self, current_synset_freqs, dataset_freqs, force_reference=False):
        # compute tf-idf
        with tictoc("tf-idf computation"):
            tfidf_freqs = []
            for doc_dict in range(len(current_synset_freqs)):
                ddict = {}
                for synset in current_synset_freqs[doc_dict]:
                    if dataset_freqs[synset] > 0:
                        ddict[synset] = current_synset_freqs[doc_dict][synset] / dataset_freqs[synset]
                    else:
                        ddict[synset] = 0

                tfidf_freqs.append(ddict)
            self.synset_tfidf_freqs.append(tfidf_freqs)

    # map a single dataset portion
    def map_dset(self, dset_words_pos, store_reference_synsets = False, force_reference_synsets = False):
        if force_reference_synsets:
            # restrict discoverable synsets to a predefined selection
            # used for the test set where not encountered synsets are unusable
            info("Restricting synsets to the reference synset set of {} entries.".format(len(self.reference_synsets)))

        current_synset_freqs = []
        with tictoc("Document-level mapping and frequency computation"):
            for wl, word_info_list in enumerate(dset_words_pos):
                debug("Semantic processing for document {}/{}".format(wl+1, len(dset_words_pos)))
                doc_freqs = {}
                for w, word_info in enumerate(word_info_list):
                    synset_activations, doc_freqs = self.get_synset(word_info, doc_freqs, force_reference_synsets)
                    if not synset_activations: continue
                if not doc_freqs:
                    warning("No synset information extracted for document {}/{}".format(wl+1, len(dset_words_pos)))
                current_synset_freqs.append(doc_freqs)

        # merge to dataset-wise synset frequencies
        with tictoc("Dataset-level frequency computation"):
            dataset_freqs, current_synset_freqs = self.doc_to_dset_freqs(current_synset_freqs, force_reference = force_reference_synsets)
            self.dataset_freqs.append(dataset_freqs)
            self.synset_freqs.append(current_synset_freqs)


        if store_reference_synsets:
            self.reference_synsets = set((dataset_freqs.keys()))

        # compute tfidf
        if self.semantic_weights == "tfidf":
            self.compute_tfidf_weights(current_synset_freqs, dataset_freqs, force_reference=force_reference_synsets)


    # apply disambiguation to choose a single semantic unit from a collection of such
    def disambiguate(self, synsets, word_information, override=None):
        disam = self.disambiguation if not override else override
        if disam == "first":
            return synsets[0]
        elif disam == 'pos':
            # take part-of-speech tags into account
            word, word_pos = word_information
            word_pos = word_pos[:2]
            # if not exist, revert to first
            if word_pos is None:
                return self.disambiguate(synsets, word_information, override="first")
            if word_pos not in self.pos_tag_mapping:
                warning("{} pos unhandled.".format(word_information))
                return self.disambiguate(synsets, word_information, override="first")
            # if encountered matching pos, get it.
            for synset in synsets:
                if synset._pos == self.pos_tag_mapping[word_pos]:
                    return synset
            # no pos match, revert to first
            return self.disambiguate(synsets, word_information, override="first")
        elif disam == "prior":
            # select the synset with the highest prior prob
            pass
        else:
            error("Undefined disambiguation method: " + self.disambiguation)


    def get_synset_from_context_embeddings(self, word):
        word_embedding = self.embbeding.get_embeddings(word)
        self.embedding.get_nearest_embedding(word_embedding)

    # generate semantic embeddings from words associated with an synset
    def compute_semantic_embeddings(self):
        if exists(self.sem_embeddings_path):
            info("Loading existing semantic embeddings from {}.".format(self.sem_embeddings_path))
            with open(self.sem_embeddings_path, "rb") as f:
                self.synset_embeddings, self.reference_synsets = pickle.load(f)
                return

        info("Computing semantic embeddings, using {} embeddings of dim {}.".format(self.embedding.name, self.semantic_embedding_dim))
        self.synset_embeddings = np.ndarray((0, self.embedding.embedding_dim), np.float32)
        for s, synset in enumerate(self.reference_synsets):
            # get the embeddings for the words of the synset
            words = self.semantic_context[synset]
            debug("Reference synset {}/{}: {}, context words: {}".format(s+1, len(self.reference_synsets), synset, len(words)))
            # get words not in cache
            word_embeddings = self.embedding.get_embeddings(words)

            # aggregate
            if self.semantic_embedding_aggr == "avg":
                embedding = np.mean(word_embeddings.as_matrix(), axis=0)
                self.synset_embeddings = np.vstack([self.synset_embeddings, embedding])
            else:
                error("Undefined semantic embedding aggregation:{}".format(self.semantic_embedding_aggr))

        # save results
        info("Writing semantic embeddings to {}".format(self.sem_embeddings_path))
        with open(self.sem_embeddings_path, "wb") as f:
            pickle.dump([self.synset_embeddings, self.reference_synsets], f)


    # function to map words to wordnet concepts
    def map_text(self, embedding, dataset):
        self.embedding = embedding
        if self.loaded_preprocessed or self.embedding.loaded_enriched():
            return

        # process semantic embeddings, if applicable
        if self.disambiguation == "context-embedding":
            self.compute_semantic_embeddings()


        # process the data
        dataset_pos = dataset.get_pos(embedding.get_present_word_indexes())
        self.synset_freqs = []
        for d, dset in enumerate(dataset_pos):
            info("Extracting {} semantic information from dataset {}/{}".format(self.name, d+1, len(dataset_pos)))
            # process data within a dataset portion
            # should store reference synsets in the train portion (d==0), but only if a reference has not
            # been already defined, e.g. via semantic embedding precomputations
            store_as_reference = (d==0 and not self.reference_synsets)
            # should force mapping to the reference if a reference has been defined
            force_reference = bool(self.reference_synsets)
            self.map_dset(dset, store_as_reference, force_reference)

        # write results: word assignments, raw, dataset-wise and tf-idf weights
        info("Writing semantic assignment results to {}.".format(self.serialization_path))
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed())
        info("Semantic mapping completed.")

    def get_all_preprocessed(self):
        return [self.assignments, self.synset_freqs, self.dataset_freqs, self.synset_tfidf_freqs, self.reference_synsets]

    def get_raw_path(self):
        return None

    def restrict_to_reference(self, do_force, activations):
        if do_force:
            activations = {s: activations[s] for s in activations \
                                  if s in self.reference_synsets}
        return activations
    def update_frequencies(self, activations, frequencies):
        for s, act in activations.items():
            if s not in frequencies:
                frequencies[s] = 0
            frequencies[s] += act
        return frequencies

    # function to get a synset from a word, using the wordnet api
    # and a local word cache. Updates synset frequencies as well.
    def get_synset(self, word_information, freqs, force_reference_synsets = False):
        word, _ = word_information
        if word_information in self.word_synset_lookup_cache:
            synset_activations = self.word_synset_lookup_cache[word_information]
            synset_activations = self.restrict_to_reference(force_reference_synsets, synset_activations)
            freqs = self.update_frequencies(synset_activations, freqs)
        else:
            # not in cache, extract
            if self.disambiguation == "embedding-context":
                # discover synset from its generated embedding and the word embedding
                synset = self.get_synset_from_context_embeddings(word)
            else:
                # look in wordnet 
                synset_activations = self.lookup_wordnet(word_information)

            synset_activations = self.restrict_to_reference(force_reference_synsets, synset_activations)

            if not synset_activations:
                return None, freqs
            freqs = self.update_frequencies(synset_activations, freqs)
            # populate cache
            self.word_synset_lookup_cache[word_information] = synset_activations

        if word not in self.assignments:
            self.assignments[word] = synset_activations
        return synset_activations, freqs


    def lookup_wordnet(self, word_information):
        word, _  = word_information
        synsets = wn.synsets(word)
        if not synsets:
            return {}
        synset = self.disambiguate(synsets, word_information)
        activations = {synset._name: 1}
        if self.do_spread_activation:
            # climb the hypernym ladder
            hyper_activations = self.spread_activation(synset, self.spread_steps, self.spread_decay)
            debug("Semantic activations (standard/spreaded): {} / {}".format(activations, hyper_activations))
            activations = {**activations, **hyper_activations}
        return activations

    def spread_activation(self, synset, steps_to_go, current_decay):
        if steps_to_go == 0:
            return
        activations = {}
        # current weight value
        new_decay = current_decay * self.spread_decay
        # get hypernyms of synset
        for hyper in synset.hypernyms():
            activations[hyper._name] = current_decay
            hypers = self.spread_activation(hyper, steps_to_go-1, new_decay)
            if hypers:
                activations = {**activations, **hypers}
        return activations


    def get_vectors(self):
        return self.semantic_document_vectors

    # get semantic vector information, wrt to the configuration
    def generate_vectors(self):
        if self.loaded_vectorized or self.embedding.loaded_enriched():
            return
        # map dicts to vectors
        if not self.dataset_freqs:
            error("Attempted to generate semantic vectors from empty containers")
        info("Getting {} semantic data.".format(self.semantic_weights))
        synset_order = sorted(self.reference_synsets)
        self.dimension = len(synset_order)
        semantic_document_vectors = [np.ndarray((0, self.dimension), np.float32) for _ in range(len(self.synset_freqs)) ]

        if self.semantic_weights  == "frequencies":
            # get raw semantic frequencies
            for d in range(len(self.synset_freqs)):
                for doc_dict in self.synset_freqs[d]:
                    doc_vector = np.asarray([[doc_dict[s] if s in doc_dict else 0 for s in synset_order]], np.float32)
                    semantic_document_vectors[d] = np.append(semantic_document_vectors[d], doc_vector, axis=0)

        elif self.semantic_weights == "tfidf":
            # get tfidf weights
            for d in range(len(self.synset_tfidf_freqs)):
                for doc_dict in self.synset_tfidf_freqs[d]:
                    doc_vector = np.asarray([[doc_dict[s] if s in doc_dict else 0 for s in synset_order]], np.float32)
                    semantic_document_vectors[d] = np.append(semantic_document_vectors[d], doc_vector, axis=0)

        elif self.semantic_weights == "embeddings":
            if self.disambiguation != "context-embedding":
                error("Embedding information requires the context-embedding semantic disambiguation. It is {} instead.".format(self.disambiguation))
            # get raw semantic frequencies
            for d in range(len(self.synset_freqs)):
                for doc_index, doc_dict in enumerate(self.synset_freqs[d]):
                    doc_sem_embeddings = np.ndarray((0, self.semantic_embedding_dim), np.float32)
                    if not doc_dict:
                        warning("Attempting to get semantic embedding vectors of document {}/{} with no semantic mappings. Defaulting to zero vector.".format(doc_index+1, len(self.synset_freqs[d])))
                        doc_vector = np.zeros((self.semantic_embedding_dim,), np.float32)
                    else:
                        # gather semantic embeddings of all document synsets
                        for synset in doc_dict:
                            synset_index = synset_order.index(synset)
                            doc_sem_embeddings = np.vstack([doc_sem_embeddings, self.synset_embeddings[synset_index, :]])
                        # aggregate them
                        if self.semantic_embedding_aggr == "avg":
                            doc_vector = np.mean(doc_sem_embeddings, axis=0)
                    semantic_document_vectors[d] = np.append(semantic_document_vectors[d], doc_vector, axis=0)
        else:
            error("Unimplemented semantic vector method: {}.".format(self.semantic_weights))

        self.semantic_document_vectors = semantic_document_vectors
        write_pickled(self.serialization_path_vectorized, [self.semantic_document_vectors, synset_order])


class GoogleKnowledgeGraph(SemanticResource):
    query_url = 'https://kgsearch.googleapis.com/v1/entities:search'
    key = None

    def __init__(self, config):
        self.config = config
        self.key = config.misc.keys["googleapi"]
        self.query_params = {
            'limit': 10,
            'indent': True,
            'key': self.key,
        }

        SemanticResource.__init__(self, config)
    def lookup(self, candidate):
        self.query_params["query"] = candidate
        url = self.query_url + '?' + urllib.parse.urlencode(self.query_params)
        response = json.loads(urllib.request.urlopen(url).read())
        for element in response['itemListElement']:
            print(element['result']['name'] + ' (' + str(element['resultScore']) + ')')
            score = element['resultScore']



class PPDB:
    # ppdb reading code:
    # https://github.com/erickrf/ppdb
    pass

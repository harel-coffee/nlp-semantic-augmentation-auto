from os.path import join, exists, splitext, basename, dirname
from os import listdir, makedirs
from dataset import Dataset
import pickle
import nltk
from utils import tictoc, error, info, debug, warning, write_pickled, read_pickled, shapes_list
import numpy as np
from serializable import Serializable
from nltk.corpus import wordnet as wn
from nltk.corpus import framenet as fn
import json
import urllib
from scipy import spatial
from bag import Bag, TFIDF

import defs


class SemanticResource(Serializable):
    dir_name = "semantic"
    semantic_name = None
    name = None
    do_spread_activation = False
    loaded_vectorized = False

    lookup_cache = {}
    word_concept_embedding_cache = {}

    concept_freqs = []
    concept_context_word_threshold = None

    reference_concepts = None
    do_limit = False

    disambiguation = None
    pos_tag_mapping = {}
    representation = None

    def get_appropriate_config_names(self):
        semantic_names = []
        # + no filtering, if filtered is specified
        filter_vals = [defs.limit.none]
        if self.do_limit:
            filter_vals.append(defs.limit.to_string(self.config.semantic.limit))
        # any combo of weights, since they're all stored
        weight_vals = defs.weights.avail()
        for w in weight_vals:
            for f in filter_vals:
                semantic_names.append(
                    SemanticResource.get_semantic_name(self.config, filtering=f, sem_weights=w))
                debug("Semantic config candidate: {}".format(semantic_names[-1]))

        return semantic_names

    def __init__(self, config):
        Serializable.__init__(self, self.dir_name)
        self.set_parameters()
        config_names = self.get_appropriate_config_names()
        for s, semantic_name in enumerate(config_names):
            # debug("Attempting to load semantic info from source {}/{}: {}".format(s + 1, len(config_names), semantic_name))
            self.semantic_name = semantic_name
            self.form_name()
            self.set_serialization_params()
            # add extras
            self.serialization_path_vectorized = self.serialization_path_preprocessed + ".vectorized"
            self.data_paths.insert(0, self.serialization_path_vectorized)
            self.read_functions.insert(0, read_pickled)
            self.handler_functions.insert(0, self.handle_vectorized)
            self.set_resources()
            self.acquire_data()
            if self.loaded():
                info("Loaded semantic info by using name: {}".format(semantic_name))
                break
        # restore correct config
        self.semantic_name = SemanticResource.get_semantic_name(self.config)
        info("Restored specifid semantic name to : {}".format(self.semantic_name))
        self.form_name()

    def create(config):
        name = config.semantic.name
        if name == Wordnet.name:
            return Wordnet(config)
        if name == GoogleKnowledgeGraph.name:
            return GoogleKnowledgeGraph(config)
        if name == ContextEmbedding.name:
            return ContextEmbedding(config)
        if name == Framenet.name:
            return Framenet(config)
        error("Undefined semantic resource: {}".format(name))
    pass

    def get_vectors(self):
        return self.semantic_document_vectors

    def generate_vectors(self):
        if self.loaded_vectorized:
            info("Skipping generating, since loaded vectorized data already.")
            return
        if self.representation.loaded_enriched():
            info("Skipping generating, since loaded enriched data already.")
            return
        # map dicts to vectors
        with tictoc("Generation of [{}] semantic vectors".format(self.semantic_weights)):
            self.concept_order = sorted(self.reference_concepts)
            self.dimension = len(self.concept_order)
            self.semantic_document_vectors = [np.ndarray((0, self.dimension), np.float32) for _ in range(len(self.concept_freqs))]

            if self.semantic_weights == "embeddings":
                error("Embedding information requires the context_embedding semantic disambiguation. It is {} instead.".format(
                    self.disambiguation), condition=self.disambiguation != "context_embedding")
                self.semantic_document_vectors = self.get_semantic_embeddings()
            elif self.semantic_weights in [defs.weights.frequencies, defs.weights.tfidf]:
                # get concept-wise frequencies
                for dset_idx in range(len(self.concept_freqs)):
                    for doc_dict in self.concept_freqs[dset_idx]:
                        sem_doc_vector = np.asarray([[doc_dict[s] if s in doc_dict else 0 for s in self.concept_order]], np.float32)
                        self.semantic_document_vectors[dset_idx] = np.append(self.semantic_document_vectors[dset_idx], sem_doc_vector, axis=0)
            else:
                error("Unimplemented semantic vector method: {}.".format(self.semantic_weights))

            write_pickled(self.serialization_path_vectorized, [self.semantic_document_vectors, self.concept_order])

    # function to get a concept from a word, using the wordnet api
    # and a local word cache. Updates concept frequencies as well.
    def get_concept(self, word_information):
        if word_information in self.lookup_cache:
            concept_activations = self.lookup_cache[word_information]
            # debug("Cache hit! for {}".format(word_information))
        else:
            concept_activations = self.lookup(word_information)
            if not concept_activations:
                return []
            # populate cache
            self.lookup_cache[word_information] = concept_activations
        return concept_activations

    # overridable name setter
    def get_semantic_name(config, filtering=None, sem_weights=None):
        if not config.has_semantic():
            return None
        if filtering is None:
            filtering = defs.limit.to_string(config.semantic.limit)
        if sem_weights is None:
            sem_weights = defs.weights.to_string(config.semantic.weights)
        disambig = "disam{}".format(config.semantic.disambiguation)
        semantic_name = "{}_{}_{}_{}".format(config.semantic.name, sem_weights, filtering, disambig)
        if config.semantic.spreading_activation:
            steps, decay = config.semantic.spreading_activation
            semantic_name += "_spread{}-{}".format(steps, decay)
        return semantic_name

    # vectorized data handler
    def handle_vectorized(self, data):
        self.semantic_document_vectors, self.concept_order = data
        self.loaded_preprocessed = True
        self.loaded_vectorized = True
        debug("Read vectorized concept docs shapes: {}, {} and concept order: {}".format(*shapes_list(self.semantic_document_vectors), len(self.concept_order)))

    # preprocessed data getter
    def get_all_preprocessed(self):
        return [self.concept_freqs, self.global_freqs, self.reference_concepts]

    def lookup(self, candidate):
        error("Attempted to lookup from the base class")

    def set_parameters(self):
        if self.config.semantic.limit is not None:
            self.do_limit = True
            self.limit_type, self.limit_number = self.config.semantic.limit

        self.semantic_weights = self.config.semantic.weights
        self.semantic_unit = self.config.semantic.unit
        self.disambiguation = self.config.semantic.disambiguation.lower()
        if self.config.semantic.spreading_activation:
            self.do_spread_activation = True
            self.spread_steps, self.spread_decay = self.config.semantic.spreading_activation[0], \
                self.config.semantic.spreading_activation[1]

        self.dataset_name = Dataset.get_limited_name(self.config)
        self.semantic_name = SemanticResource.get_semantic_name(self.config)
        self.form_name()

    # make name string from components
    def form_name(self):
        self.name = "{}_{}".format(self.dataset_name, self.semantic_name)

    def assign_representation(self, representation):
        self.representation = representation

    # apply disambiguation to choose a single semantic unit from a collection of such
    def disambiguate(self, concepts, word_information, override=None):
        disam = self.disambiguation if not override else override
        if disam == "first":
            return [concepts[0]]
        elif disam == 'pos':
            # take part-of-speech tags into account
            word, word_pos = word_information
            word_pos = word_pos[:2]
            # if not exist, revert to first
            if word_pos is None:
                return self.disambiguate(concepts, word_information, override="first")
            if word_pos not in self.pos_tag_mapping:
                return self.disambiguate(concepts, word_information, override="first")
            # if encountered matching pos, get it.
            for concept in concepts:
                if concept._pos == self.pos_tag_mapping[word_pos]:
                    return [concept]
            # no pos match, revert to first
            return self.disambiguate(concepts, word_information, override="first")
        else:
            error("Undefined disambiguation method: " + self.disambiguation)

    def get_cache_path(self):
        return join(self.config.folders.raw_data, self.dir_name, self.base_name + ".cache.pickle")

    # read existing resource-wise serialized semantic cache from previous runs to speedup resolving
    def load_semantic_cache(self):
        cache_path = self.get_cache_path()
        if exists(cache_path):
            self.lookup_cache = read_pickled(cache_path)
            info("Read a {}-long semantic cache from {}.".format(len(self.lookup_cache), cache_path))
            return self.lookup_cache
        return {}

    # write the semantic cache after resolution of the current dataset
    def write_semantic_cache(self):
        cache_path = self.get_cache_path()
        if not exists(dirname(cache_path)):
            makedirs(dirname(cache_path), exist_ok=True)
        info("Writing a {}-long semantic cache to {}.".format(len(self.lookup_cache), cache_path))
        write_pickled(cache_path, self.lookup_cache)

    # function to map words to wordnet concepts
    def map_text(self, representation, dataset):
        self.representation = representation
        if self.representation.loaded_enriched():
            info("Skipping mapping text due to enriched data already loaded.")
            return
        if self.loaded_vectorized:
            info("Skipping mapping text due to vectorized data already loaded.")
            return
        if self.loaded_preprocessed:
            if self.semantic_weights == defs.weights.frequencies:
                info("Skipping mapping text due to preprocessed data already loaded.")
                return
            if self.semantic_weights == defs.weights.tfidf:
                # compute TFIDF
                info("Computing TFIDF from the loaded preprocessed raw frequencies.")
                # train
                bag = TFIDF()
                bag.idf_normalize((self.concept_freqs[0], self.global_freqs[0]))
                self.concept_freqs[0] = bag.get_weights()
                # test
                bag = TFIDF()
                bag.idf_normalize((self.concept_freqs[1], self.global_freqs[1]))
                self.concept_freqs[1] = bag.get_weights()
                return
        if self.semantic_weights == defs.weights.tfidf:
            bag_train, bag_test = TFIDF(), TFIDF()
        else:
            bag_train, bag_test = Bag(), Bag()

        # read the semantic resource input-concept cache , if it exists
        self.load_semantic_cache()

        # process the dataset
        # train
        info("Extracting {} semantic information from the training dataset".format(self.name))
        bag_train.set_element_processing_function(self.get_concept)
        if self.do_limit:
            bag_train.set_token_filtering(self.limit_type, self.limit_number)
        bag_train.map_collection(dataset.train)
        self.reference_concepts = bag_train.get_token_list()

        # test - since we restrict to the training set concepts, no need to filter
        bag_test.set_token_list(self.reference_concepts)
        bag_test.set_element_processing_function(self.get_concept)
        bag_test.map_collection(dataset.test)

        # collect vectors

        self.concept_freqs = [bag_train.get_weights(), bag_test.get_weights()]
        self.global_freqs = [bag_train.get_global_weights(), bag_test.get_global_weights()]

        # write results
        info("Writing semantic assignment results to {}.".format(self.serialization_path_preprocessed))
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed())

        # store the cache
        self.write_semantic_cache()

    def spread_activation(self, synset, steps_to_go, current_decay):
        error("Attempted to call abstract spread activation for semantic resource {}.".format(self.name))

    def handle_preprocessed(self, preprocessed):
        self.loaded_preprocessed = True
        self.concept_freqs, self.global_freqs, self.reference_concepts = preprocessed
        debug("Read preprocessed concept docs shapes: {}, {}".format(*list(map(len, self.concept_freqs))))


class Wordnet(SemanticResource):
    name = "wordnet"

    def __init__(self, config):
        self.config = config
        self.base_name = self.name
        SemanticResource.__init__(self, config)

        # map nltk pos maps into meaningful wordnet ones
        self.pos_tag_mapping = {"VB": wn.VERB, "NN": wn.NOUN, "JJ": wn.ADJ, "RB": wn.ADV}

    def fetch_raw(self, dummy_input):
        if self.base_name not in listdir(nltk.data.find("corpora")):
            nltk.download("wordnet")
        return None

    def handle_raw_serialized(self, raw_serialized):
        pass

    def handle_raw(self, raw_data):
        pass

    def lookup(self, word_information):
        word, _ = word_information
        synsets = wn.synsets(word)
        if not synsets:
            return {}
        synsets = self.disambiguate(synsets, word_information)
        activations = {synset._name: 1 for synset in synsets}
        if self.do_spread_activation:
            # climb the hypernym ladder
            hyper_activations = self.spread_activation(synsets, self.spread_steps, 1)
            activations = {**activations, **hyper_activations}
        return activations

    def spread_activation(self, synsets, steps_to_go, current_decay):
        if steps_to_go == 0:
            return
        activations = {}
        # current weight value
        new_decay = current_decay * self.spread_decay
        for synset in synsets:
            # get hypernyms of synset
            for hyper in synset.hypernyms():
                activations[hyper._name] = current_decay
                hypers = self.spread_activation([hyper], steps_to_go - 1, new_decay)
                if hypers:
                    activations = {**activations, **hypers}
        return activations


class ContextEmbedding(SemanticResource):
    name = "context"

    def __init__(self, config):
        self.config = config
        # incompatible with embedding training
        error("Embedding context data missing: {}".format("Embedding train mode incompatible with semantic embeddings."),
              self.config.representation.name == "train")
        # read specific params
        self.embedding_aggregation = self.config.semantic.context_aggregation
        self.representation_dim = self.config.representation.dimension
        self.context_threshold = self.config.semantic.context_threshold
        self.context_file = self.config.semantic.context_file
        # calculate the synset embeddings path
        SemanticResource.__init__(self, config)
        if not any([x for x in self.load_flags]):
            error("Failed to load semantic embeddings context.")

    def form_name(self):
        SemanticResource.form_name(self)
        thr = ""
        if self.context_threshold:
            thr += "_thresh{}".format(self.context_threshold)
        self.name += "_ctx{}_emb{}{}".format(basename(splitext(self.context_file)[0]), self.config.representation.name, thr)

    def get_raw_path(self):
        return self.context_file

    def handle_raw(self, raw_data):
        self.semantic_context = {}
        # apply word frequency thresholding, if applicable
        if self.context_threshold is not None:
            num_original = len(raw_data.items())
            info("Limiting the {} reference context concepts with a word frequency threshold of {}".format(num_original, self.context_threshold))
            self.semantic_context = {s: wl for (s, wl) in raw_data.items() if len(wl) >= self.context_threshold}
            info("Ended up with context information for {} concepts.".format(len(self.semantic_context)))
        else:
            self.semantic_context = raw_data
        # set the loaded concepts as the reference concept list
        self.reference_concepts = list(sorted(self.semantic_context.keys()))
        if not self.reference_concepts:
            error("Frequency threshold of synset context: {} resulted in zero reference concepts".format(self.context_threshold))
        info("Applied {} reference concepts from pre-extracted synset words".format(len(self.reference_concepts)))
        return self.semantic_context

        # serialize
        write_pickled(self.sem_embeddings_path, raw_data)

    def fetch_raw(self, path):
        # load the concept-words list
        error("Embedding context data missing: {}".format(self.context_file), not exists(self.context_file))
        with open(self.context_file, "rb") as f:
            data = pickle.load(f)
        return data

    def map_text(self, embedding, dataset):
        self.embedding = embedding
        self.compute_semantic_embeddings()
        # kdtree for fast lookup
        self.kdtree = spatial.KDTree(self.concept_embeddings)
        SemanticResource.map_text(self, embedding, dataset)

    def lookup(self, candidate):
        word, _ = candidate
        if word in self.word_concept_embedding_cache:
            concept = self.word_concept_embedding_cache[word]
        else:
            word_embedding = self.embedding.get_embeddings([word])
            _, conc_idx = self.kdtree.query(word_embedding)
            if conc_idx is None or len(conc_idx) == 0:
                return {}
            concept = self.reference_concepts[int(conc_idx)]
        # no spreading activation defined here.
        return {concept: 1}

    def handle_raw_serialized(self, raw_serialized):
        self.loaded_raw_serialized = True
        self.concept_embeddings, self.reference_concepts = raw_serialized
        debug("Read concept embeddings shape: {}".format(self.concept_embeddings.shape))

    # generate semantic embeddings from words associated with an concept
    def compute_semantic_embeddings(self):
        if self.loaded_raw_serialized:
            return
        info("Computing semantic embeddings, using {} embeddings of dim {}.".format(self.embedding.name, self.representation_dim))
        retained_reference_concepts = []
        self.concept_embeddings = np.ndarray((0, self.representation_dim), np.float32)
        for s, concept in enumerate(self.reference_concepts):
            # get the embeddings for the words in the concept's context
            words = self.semantic_context[concept]
            debug("Reference concept {}/{}: {}, context words: {}".format(s + 1, len(self.reference_concepts), concept, len(words)))
            word_embeddings = self.embedding.get_embeddings(words)
            if len(word_embeddings) == 0:
                continue
            # aggregate
            if self.embedding_aggregation == "avg":
                embedding = np.mean(word_embeddings.as_matrix(), axis=0)
                self.concept_embeddings = np.vstack([self.concept_embeddings, embedding])
            else:
                error("Undefined semantic embedding aggregation:{}".format(self.embedding_aggregation))

            retained_reference_concepts.append(concept)

        num_dropped = len(self.reference_concepts) - len(retained_reference_concepts)
        if num_dropped > 0:
            info("Discarded {} / {} concepts resulting in {}, due to no context words existing in read embeddings.".format(num_dropped, len(self.reference_concepts), len(retained_reference_concepts)))
        self.reference_concepts = retained_reference_concepts
        # save results
        info("Writing semantic embeddings to {}".format(self.serialization_path))
        write_pickled(self.serialization_path, [self.concept_embeddings, self.reference_concepts])
        self.loaded_raw_serialized = True

    def get_semantic_embeddings(self):
        semantic_document_vectors = np.ndarray((0, self.representation_dim), np.float32)
        # get raw semantic frequencies
        for d in range(len(self.concept_freqs)):
            for doc_index, doc_dict in enumerate(self.concept_freqs[d]):
                doc_sem_embeddings = np.ndarray((0, self.semantic_representation_dim), np.float32)
                if not doc_dict:
                    warning("Attempting to get semantic embedding vectors of document {}/{} with no semantic mappings. Defaulting to zero vector.".format(doc_index + 1, len(self.concept_freqs[d])))
                    doc_vector = np.zeros((self.semantic_representation_dim,), np.float32)
                else:
                    # gather semantic embeddings of all document concepts
                    for concept in doc_dict:
                        concept_index = self.concept_order.index(concept)
                        doc_sem_embeddings = np.vstack([doc_sem_embeddings, self.concept_embeddings[concept_index, :]])
                    # aggregate them
                    if self.semantic_embedding_aggregation == "avg":
                        doc_vector = np.mean(doc_sem_embeddings, axis=0)
                semantic_document_vectors[d].append(doc_vector)


class GoogleKnowledgeGraph(SemanticResource):
    name = "googlekt"

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
        word, pos_info = candidate
        self.query_params["query"] = word
        url = self.query_url + '?' + urllib.parse.urlencode(self.query_params)
        response = json.loads(urllib.request.urlopen(url).read())
        names, hypers, scores = [], [], []
        for element in response['itemListElement']:
            results = element['result']
            if "name" not in results:
                continue
            scores.append(element['resultScore'])
            names.append(results['name'])
            hypers.append(results['@type'])
            # descr = results['description']
            # detailed_descr = results['detailedDescription'] if 'detailedDescription' in results else None

        names = self.disambiguate(names, candidate)
        activations = {n: 1 for n in names}

        if self.do_spread_activation:
            for name in names:
                idx = names.index(name)
                hyps = hypers[idx]
                activations[name] = 1
                if self.do_spread_activation:
                    current_decay = self.spread_decay
                    for h, hyp in enumerate(hyps):
                        if h + 1 > self.spread_steps:
                            break
                        activations[hyp] = current_decay
                        current_decay *= self.spread_decay
        return activations


class Framenet(SemanticResource):
    name = "framenet"
    relations_to_spread = ["Inheritance"]

    def __init__(self, config):
        self.config = config
        self.base_name = self.name
        SemanticResource.__init__(self, config)
        # map nltk pos maps into meaningful framenet ones
        self.pos_tag_mapping = {"VB": "V", "NN": "N", "JJ": "A", "RB": "ADV"}

    def fetch_raw(self, dummy_input):
        if not self.base_name + "_v17" in listdir(nltk.data.find("corpora")):
            nltk.download("framenet_v17")
        return None

    def lookup(self, candidate):
        # http://www.nltk.org/howto/framenet.html
        word, word_pos = candidate
        # in framenet, pos-disambiguation is done via the lookup
        if self.disambiguation == defs.semantic.disam.pos:
            frames = self.lookup_with_POS(candidate)
        else:
            frames = fn.frames_by_lemma(word)
            if not frames:
                return None
            frames = self.disambiguate(frames, candidate)
        if not frames:
            return None
        activations = {x.name: 1 for x in frames}
        if self.do_spread_activation:
            parent_activations = self.spread_activation(frames, self.spread_steps, 1)
            activations = {**activations, **parent_activations}
        return activations

    def lookup_with_POS(self, candidate):
        word, word_pos = candidate
        if word_pos in self.pos_tag_mapping:
            word += "." + self.pos_tag_mapping[word_pos]
        frames = fn.frames_by_lemma(word)
        if not frames:
            return None
        return self.disambiguate(frames, candidate, override=defs.semantic.disam.first)

    def get_related_frames(self, frame):
        # get just parents
        return [fr.Parent for fr in frame.frameRelations if fr.type.name == "Inheritance" and fr.Child == frame]

    def spread_activation(self, frames, steps_to_go, current_decay):
        if steps_to_go == 0:
            return
        activations = {}
        current_decay *= self.spread_decay
        for frame in frames:
            related_frames = self.get_related_frames(frame)
            for rel in related_frames:
                activations[rel.name] = current_decay
                parents = self.spread_activation([rel], steps_to_go - 1, current_decay)
                if parents:
                    activations = {**activations, **parents}
        return activations


class BabelNet:
    name = "babelnet"

    def get_raw_path(self):
        return None

    def __init__(self, config):
        self.config = config
        self.base_name = self.name
        SemanticResource.__init__(self, config)
        # map nltk pos maps into meaningful framenet ones
        self.pos_tag_mapping = {"VB": "V", "NN": "N", "JJ": "A", "RB": "ADV"}

    # lookup for babelnet should be about a (large) set of words
    # written into a file, read by the java api
    # results written into a file (json), read from here.
    # run calls the java program

import random
import tqdm
import numpy as np
from os import listdir
# from nltk.tokenize import RegexpTokenizer
from utils import error, tictoc, info, write_pickled, align_index, debug, warning
from sklearn.datasets import fetch_20newsgroups
from sklearn.model_selection import StratifiedShuffleSplit
from nltk.corpus import stopwords, reuters
from nltk.stem import WordNetLemmatizer, PorterStemmer
from nltk.tokenize import word_tokenize, sent_tokenize
import string
import nltk
from semantic import Wordnet

from serializable import Serializable


class Dataset(Serializable):
    name = ""
    vocabulary = set()
    vocabulary_index = []
    word_to_index = {}
    limited_name = ""
    dir_name = "datasets"
    undefined_word_index = None
    preprocessed = False
    train, test = None, None
    multilabel = False
    data_names = ["train-data", "train-labels", "train-label-names",
                  "test", "test-labels", "test_label-names", "vocabulary",
                  "vocabulary_index", "undefined_word_index"]

    def create(config):
        name = config.dataset.name
        if name == TwentyNewsGroups.name:
            return TwentyNewsGroups(config)
        elif name == Reuters.name:
            return Reuters(config)
        else:
            error("Undefined dataset: {}".format(name))

    @staticmethod
    def generate_name(config):
        name = config.dataset.name
        if config.dataset.prepro is not None:
            name += "_" + config.dataset.prepro
        return name

    # dataset creation
    def __init__(self):
        Serializable.__init__(self, self.dir_name)
        self.set_serialization_params()
        # check for limited dataset
        self.apply_limit()
        self.acquire_data()
        if self.loaded():
            # downloaded successfully
            self.loaded_index = self.load_flags.index(True)
        else:
            # check for raw dataset. Suspend limit and setup paths
            self.suspend_limit()
            self.set_serialization_params()
            # exclude loading of pre-processed data
            self.data_paths = self.data_paths[1:]
            self.read_functions = self.read_functions[1:]
            self.handler_functions = self.handler_functions[1:]
            # get the data but do not preprocess
            self.acquire_data()
            self.loaded_index = self.load_flags.index(True)
            # reapply the limit
            self.apply_limit()

        self.config.dataset.full_name = self.name

    def handle_preprocessed(self, preprocessed):
        info("Loaded preprocessed {} dataset from {}.".format(self.name, self.serialization_path_preprocessed))
        self.train, self.train_target, self.train_label_names, \
            self.test, self.test_target, self.test_label_names, \
            self.vocabulary, self.vocabulary_index, self.undefined_word_index \
            = [preprocessed[name] for name in self.data_names]

        self.num_labels = len(self.train_label_names)
        for index, word in enumerate(self.vocabulary):
            self.word_to_index[word] = index
        info("Loaded preprocessed data: {} train, {} test, with {} labels".format(len(self.train), len(self.test), self.num_labels))
        self.loaded_preprocessed = True

    def suspend_limit(self):
        self.name = Dataset.generate_name(self.config)

    def is_multilabel(self):
        return self.multilabel

    def get_raw_path(self):
        error("Need to override raw path datasea getter for {}".format(self.name))

    def fetch_raw(self):
        error("Need to override raw data fetcher for {}".format(self.name))

    def handle_raw(self, raw_data):
        error("Need to override raw data handler for {}".format(self.name))

    def handle_raw_serialized(self, deserialized_data):
        self.train, self.train_target, self.train_label_names, \
            self.test, self.test_target, self.test_label_names = deserialized_data
        self.num_labels = len(set(self.train_label_names))
        self.loaded_raw_serialized = True

    # data getter
    def get_data(self):
        return self.train, self.test

    def get_targets(self):
        return self.train_target, self.test_target

    def get_num_labels(self):
        return self.num_labels

    # static method for external name computation
    @staticmethod
    def get_limited_name(config):
        name = config.dataset.name
        if config.has_class_limit():
            name += "_clim_{}".format(config.dataset.class_limit)
        if config.has_data_limit():
            ltrain, ltest = config.dataset.data_limit
            if ltrain:
                name += "_dlim_tr{}".format(ltrain)
            if ltest:
                name += "_dlim_te{}".format(ltest)
        return name

    def apply_data_limit(self, name):
        ltrain, ltest = self.config.dataset.data_limit
        if ltrain:
            name += "_dlim_tr{}".format(ltrain)
            if self.train:
                try:
                    # use stratification
                    ratio = ltrain / len(self.train)
                    splitter = StratifiedShuffleSplit(1, test_size=ratio)
                    splits = list(splitter.split(np.zeros(len(self.train)), self.train_target))
                    self.train = [self.train[n] for n in splits[0][1]]
                    self.train_target = [self.train_target[n] for n in splits[0][1]]
                    info("Limited {} loaded data to {} train items.".format(self.base_name, len(self.train)))
                except ValueError as ve:
                    warning(ve)
                    warning("Resorting to non-stratified limiting")
                    self.train = self.train[:ltrain]
                    self.train_target = self.train_target[:ltrain]
        if ltest:
            name += "_dlim_te{}".format(ltest)
            if self.test:
                try:
                    # use stratification
                    ratio = ltest / len(self.test)
                    splitter = StratifiedShuffleSplit(1, test_size=ratio)
                    splits = list(splitter.split(np.zeros(len(self.test)), self.test_target))
                    self.test = [self.test[n] for n in splits[0][1]]
                    self.test_target = [self.test_target[n] for n in splits[0][1]]
                    info("Limited {} loaded data to {} test items.".format(self.base_name, len(self.test)))
                except ValueError as ve:
                    warning(ve)
                    warning("Resorting to non-stratified limiting")
                    self.test = self.test[:ltest]
                    self.test_target = self.test_target[:ltest]
        return name

    def restrict_to_classes(self, data, labels, restrict_classes):
        new_data, new_labels = [], []
        for d, l in zip(data, labels):
            if self.multilabel:
                rl = list(set(l).intersection(restrict_classes))
                if not rl:
                    continue
            else:
                rl = l if l in restrict_classes else None
            if not rl:
                continue
            new_data.append(d)
            new_labels.append(rl)
        return new_data, new_labels

    def apply_class_limit(self, name):
        c_lim = self.config.dataset.class_limit
        if c_lim is not None:
            name += "_clim_{}".format(c_lim)
            if self.train:
                # data have been loaded -- apply limit
                retained_classes = random.sample(list(range(self.num_labels)), c_lim)
                info("Limiting to the {} classes: {}".format(c_lim, retained_classes))
                if self.multilabel:
                    debug("Max train/test labels per item prior: {} {}".format(max(map(len, self.train_target)), max(map(len, self.test_target))))
                self.train, self.train_target = self.restrict_to_classes(self.train, self.train_target, retained_classes)
                self.test, self.test_target = self.restrict_to_classes(self.test, self.test_target, retained_classes)
                self.num_labels = len(retained_classes)
                # remap retained classes to indexes starting from 0
                self.train_target = align_index(self.train_target, retained_classes)
                self.test_target = align_index(self.test_target, retained_classes)
                # fix the label names
                self.train_label_names = [self.train_label_names[rc] for rc in retained_classes]
                self.test_label_names = [self.test_label_names[rc] for rc in retained_classes]
                info("Limited {} dataset to {} classes: {} - i.e. {} - resulting in {} train and {} test data."
                     .format(self.base_name, self.num_labels, retained_classes,
                             self.train_label_names, len(self.train), len(self.test)))
                if self.multilabel:
                    debug("Max train/test labels per item post: {} {}".format(max(map(len, self.train_target)), max(map(len, self.test_target))))
        return name

    def apply_limit(self):
        if self.config.has_limit():
            self.base_name = Dataset.generate_name(self.config)
            name = self.apply_class_limit(self.base_name)
            self.name = self.apply_data_limit(name)
            self.set_paths_by_name(self.name)
        if self.train:
            # serialize the limited version
            write_pickled(self.serialization_path, self.get_all_raw())

    def setup_nltk_resources(self):
        try:
            stopwords.words(self.language)
        except LookupError:
            nltk.download("stopwords")

        self.stopwords = set(stopwords.words(self.language))
        try:
            nltk.pos_tag("Text")
        except LookupError:
            nltk.download('averaged_perceptron_tagger')
        try:
            [x("the quick brown. fox! jumping-over lazy, dog.") for x in [word_tokenize, sent_tokenize]]
        except LookupError as err:
            nltk.download("punkt")

        # setup word prepro
        while True:
            try:
                if self.config.dataset.prepro == "stem":
                    self.stemmer = PorterStemmer()
                    self.word_prepro = lambda w_pos: (self.stemmer.stem(w_pos[0]), w_pos[1])
                elif self.config.dataset.prepro == "lemma":
                    self.lemmatizer = WordNetLemmatizer()
                    self.word_prepro = self.apply_lemmatizer
                else:
                    self.word_prepro = lambda x: x
                break
            except LookupError as err:
                error(err)
        # punctuation
        self.punctuation_remover = str.maketrans('', '', string.punctuation)

    def apply_lemmatizer(self, w_pos):
        wordnet_pos = Wordnet.get_wordnet_pos(w_pos[1])
        if not wordnet_pos:
            return self.lemmatizer.lemmatize(w_pos[0]), w_pos[1]
        else:
            return self.lemmatizer.lemmatize(w_pos[0], wordnet_pos), w_pos[1]

    # map text string into list of stopword-filtered words and POS tags
    def process_single_text(self, text):
        sents = sent_tokenize(text.lower())
        words = []
        for sent in sents:
            words.extend(word_tokenize(sent))
        # words = text_to_word_sequence(text, filters=filt, lower=True, split=' ')
        # words = [w.lower() for w in self.nltk_tokenizer.tokenize(text)]

        # remove stopwords and punctuation content
        words = [w.translate(self.punctuation_remover) for w in words]
        words = [w for w in words if w not in self.stopwords and w.isalpha()]
        # pos tagging
        words_with_pos = nltk.pos_tag(words)
        # stemming / lemmatization
        words_with_pos = [self.word_prepro(wp) for wp in words_with_pos]
        return words_with_pos

    # preprocess single
    def preprocess_text_collection(self, document_list, track_vocabulary=False):
        # filt = '!"#$%&()*+,-./:;<=>?@\[\]^_`{|}~\n\t1234567890'
        ret_words_pos, ret_voc = [], set()
        num_words = []
        with tqdm.tqdm(desc="Mapping document collection", total=len(document_list), ascii=True, ncols=100, unit="collection") as pbar:
            for i in range(len(document_list)):
                pbar.set_description("Document {}/{}".format(i + 1, len(document_list)))
                pbar.update()
                # text_words_pos = self.process_single_text(document_list[i], filt=filt, stopwords=stopw)
                text_words_pos = self.process_single_text(document_list[i])
                ret_words_pos.append(text_words_pos)
                if track_vocabulary:
                    ret_voc.update([wp[0] for wp in text_words_pos])
                num_words.append(len(text_words_pos))
        stats = [x(num_words) for x in [np.mean, np.var, np.std]]
        info("Words per document stats: mean {:.3f}, var {:.3f}, std {:.3f}".format(*stats))
        return ret_words_pos, ret_voc

    # preprocess raw texts into word list
    def preprocess(self):
        if self.loaded_preprocessed:
            info("Skipping preprocessing, preprocessed data already loaded from {}.".format(self.serialization_path_preprocessed))
            return
        self.setup_nltk_resources()
        with tictoc("Preprocessing {}".format(self.name)):
            info("Mapping training set to word collections.")
            self.train, self.vocabulary = self.preprocess_text_collection(self.train, track_vocabulary=True)
            info("Mapping test set to word collections.")
            self.test, _ = self.preprocess_text_collection(self.test)
            # fix word order and get word indexes
            self.vocabulary = list(self.vocabulary)
            for index, word in enumerate(self.vocabulary):
                self.word_to_index[word] = index
                self.vocabulary_index.append(index)
            # add another for the missing word
            self.undefined_word_index = len(self.vocabulary)
        # serialize
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed())

    def get_all_raw(self):
        return {"train-data": self.train, "train-labels": self.train_target, "train-label-names": self.train_label_names,
                "test": self.test, "test-labels": self.test_target, "test_label-names": self.test_label_names}

    def get_all_preprocessed(self):
        res = self.get_all_raw()
        res['vocabulary'] = self.vocabulary
        res['vocabulary_index'] = self.vocabulary_index
        res['undefined_word_index'] = self.undefined_word_index
        return res

    def get_name(self):
        return self.name


class TwentyNewsGroups(Dataset):
    name = "20newsgroups"
    language = "english"

    def fetch_raw(self, dummy_input):
        # only applicable for raw dataset
        if self.name != self.base_name:
            return None
        info("Downloading {} via sklearn".format(self.name))

        train = fetch_20newsgroups(subset='train', shuffle=True, random_state=self.config.get_seed())
        test = fetch_20newsgroups(subset='test', shuffle=True, random_state=self.config.get_seed())
        return [train, test]

    def handle_raw(self, raw_data):

        # results are sklearn bunches
        # map to train/test/categories
        train, test = raw_data
        info("Got {} and {} train / test samples".format(len(train.data), len(test.data)))
        self.train, self.test = train.data, test.data
        self.train_target, self.test_target = train.target, test.target
        self.train_label_names = train.target_names
        self.test_label_names = test.target_names
        self.num_labels = len(self.train_label_names)
        # write serialized data
        write_pickled(self.serialization_path, self.get_all_raw())
        self.loaded_raw = True

    def __init__(self, config):
        self.config = config
        self.base_name = self.name
        Dataset.__init__(self)
        self.multilabel = False

    # raw path setter
    def get_raw_path(self):
        # dataset is downloadable
        pass

class Reuters(Dataset):
    name = "reuters"
    language = "english"

    def __init__(self, config):
        self.config = config
        self.multilabel = True
        self.base_name = self.name
        Dataset.__init__(self)

    def fetch_raw(self, dummy_input):
        # only applicable for raw dataset
        if self.name != self.base_name:
            return None
        info("Downloading raw {} dataset".format(self.name))
        if not self.base_name + ".zip" in listdir(nltk.data.find("corpora")):
            nltk.download("reuters")
        # get ids
        categories = reuters.categories()
        self.num_labels = len(categories)
        self.train_label_names, self.test_label_names = [], []
        cat_idx2label_train, cat_idx2label_test = {}, {}
        train_docs, test_docs = [], []
        doc2labels = {}

        # get content
        self.train, self.test = [], []
        self.train_target, self.test_target = [], []
        for cat_index, cat in enumerate(categories):
            # get all docs in that category
            for doc in reuters.fileids(cat):
                # document to label mappings
                if doc not in doc2labels:
                    doc2labels[doc] = []
                doc2labels[doc].append(cat_index)
                # get its content
                content = reuters.raw(doc)
                # assign content
                if doc.startswith("training"):
                    train_docs.append(doc)
                    self.train.append(content)
                    if cat_index not in cat_idx2label_train:
                        cat_idx2label_train[cat_index] = cat
                else:
                    test_docs.append(doc)
                    self.test.append(content)
                    if cat_index not in cat_idx2label_test:
                        cat_idx2label_test[cat_index] = cat

        # list out labels
        for doc in train_docs:
            self.train.append(doc)
            self.train_target.append(doc2labels[doc])
        for doc in test_docs:
            self.test.append(doc)
            self.test_target.append(doc2labels[doc])

        if len(cat_idx2label_test) != len(cat_idx2label_train):
            error("{} number of label train/test mismatch: {} / {}".format(self.name, len(cat_idx2label_test), len(cat_idx2label_test)))
        if cat_idx2label_test != cat_idx2label_train:
            error("{} index-label mismatch".format(self.name, cat_idx2label_test, cat_idx2label_test))
        self.train_label_names, self.test_label_names = [[cat_idx2label_test[i] for i in cat_idx2label_test]] * 2
        self.test_label_names = list(self.test_label_names)
        # self.train_target = np.asarray(self.train_target, np.int32)
        # self.test_target = np.asarray(self.test_target, np.int32)
        return self.get_all_raw()

    def handle_raw(self, raw_data):
        # serialize
        write_pickled(self.serialization_path, raw_data)
        self.loaded_raw = True
        pass

    # raw path setter
    def get_raw_path(self):
        # dataset is downloadable
        return None

# class MultilingMMS:
#    name = "multiling-mms"
#    def get_raw_path():
#        pass
#

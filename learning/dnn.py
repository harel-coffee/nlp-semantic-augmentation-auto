from os.path import join, basename, exists
from sklearn.exceptions import UndefinedMetricWarning
import warnings
from os import makedirs
import numpy as np
from learning.classifier import Classifier
import sys

# import keras with this disgusting hack to get rid of the "Using xxxx backend" message
stderr = sys.stderr
sys.stderr = open('/dev/null', 'w')
from keras.models import Sequential, model_from_json
from keras.layers import Activation, Dense, Dropout
from keras.layers import LSTM as keras_lstm
from keras import callbacks
sys.stderr = stderr

# tf deprecation warnings
import tensorflow.python.util.deprecation as deprecation
deprecation._PRINT_DEPRECATION_WARNINGS = False

from utils import info, debug, error, write_pickled, one_hot

# from keras import backend
# import tensorflow as tf

warnings.simplefilter(action='ignore', category=UndefinedMetricWarning)


class DNN(Classifier):
    sequence_length = None

    do_multilabel = False
    train_embeddings_params = []
    do_folds = False
    do_validate_portion = False
    early_stopping = None

    model_paths = []

    def __init__(self):
        Classifier.__init__(self)
        pass

    def __str__(self):
        return "name:{} hidden dim:{} nlayers:{} sequence len:{}".format(
            self.name, self.hidden, self.layers, self.sequence_length)

    # define useful keras callbacks for the training process
    def get_callbacks(self):
        self.callbacks = []
        [makedirs(x, exist_ok=True) for x in [self.results_folder, self.models_folder]]

        self.model_path = self.get_current_model_path()

        if self.use_validation_for_training:
            # model saving with early stoppingtch_si
            weights_path = self.model_path

            # weights_path = os.path.join(models_folder,"{}_fold_{}_".format(self.name, self.fold_index) + "ep_{epoch:02d}_valloss_{val_loss:.2f}.hdf5")
            self.model_saver = callbacks.ModelCheckpoint(weights_path, monitor='val_loss', verbose=0,
                                                         save_best_only=self.validation_exists,
                                                         save_weights_only=False,
                                                         mode='auto', period=1)
            self.callbacks.append(self.model_saver)

            if self.early_stopping_patience:
                self.early_stopping = callbacks.EarlyStopping(monitor='val_loss', min_delta=0, patience=self.early_stopping_patience, verbose=0,
                                                              mode='auto', baseline=None, restore_best_weights=False)
                self.callbacks.append(self.early_stopping)

            # learning rate modifier at loss function plateaus
            self.lr_reducer = callbacks.ReduceLROnPlateau(monitor='val_loss', factor=0.1,
                                                          patience=10, verbose=0, mode='auto',
                                                          min_delta=0.0001, cooldown=0, min_lr=0)
            self.callbacks.append(self.lr_reducer)


        # stop on NaN
        self.nan_terminator = callbacks.TerminateOnNaN()
        self.callbacks.append(self.nan_terminator)

        # logging
        train_csv_logfile = join(self.results_folder, basename(self.get_current_model_path()) + "train.csv")
        self.csv_logger = callbacks.CSVLogger(train_csv_logfile, separator=',', append=False)
        self.callbacks.append(self.csv_logger)
        return self.callbacks

    # to preliminary work
    def make(self):

        # initialize rng
        # for 100% determinism, you may need to enforce CPU single-threading
        # tf.set_random_seed(self.seed)
        # session_conf = tf.ConfigProto(intra_op_parallelism_threads=1, inter_op_parallelism_threads=1)
        # sess = tf.Session(graph=tf.get_default_graph(), config=session_conf)
        # backend.set_session(sess)
        Classifier.make(self)
        pass

    # print information pertaining to early stopping
    def report_early_stopping(self):
        if self.validation_exists and self.early_stopping is not None:
            info("Stopped on epoch {}/{}".format(self.early_stopping.stopped_epoch + 1, self.epochs))
            write_pickled(self.model_path + ".early_stopping", self.early_stopping.stopped_epoch)

    # train a model on training & validation data portions
    def train_model(self, train_data, train_labels, val_data, val_labels):
        # define the model
        model = self.get_model()
        train_labels = one_hot(train_labels, self.num_labels)
        if val_data is not None:
            val_labels = one_hot(val_labels, self.num_labels)
        # train the damn thing!
        debug("Feeding the network train shapes: {} {}".format(train_data.shape, train_labels.shape))
        if val_data is not None:
            debug("Using validation shapes: {} {}".format(val_data.shape, val_labels.shape))
            val = (val_data, val_labels)
        else:
            val = None
        model.fit(train_data, train_labels,
                  batch_size=self.batch_size,
                  epochs=self.epochs,
                  validation_data=val,
                  verbose=self.verbosity,
                  callbacks=self.get_callbacks())
        self.report_early_stopping()
        return model

    # evaluate a dnn
    def test_model(self, test_data, model):
        return model.predict(test_data, batch_size=self.batch_size, verbose=self.verbosity)

    # add softmax classification layer
    def add_softmax(self, model, is_first=False):
        if is_first:
            model.add(Dense(self.num_labels, input_shape=self.input_shape, name="dense_classifier"))
        else:
            model.add(Dense(self.num_labels, name="dense_classifier"))

        model.add(Activation('softmax', name="softmax"))
        return model

    def get_model_path(self):
        return self.model_saver.filepath

    def save_model(self, model):
        if self.use_validation_for_training:
            # handled by the model saver callback
            return
        # serialize model
        model_path, weights_path = [self.get_current_model_path() + suff for suff in (".model", ".weights")]
        with open(model_path, "w") as json_file:
            json_file.write(model.to_json())
        # serialize weights (h5)
        model.save_weights(weights_path)

    def load_model(self):
        path = self.get_current_model_path()
        model_path, weights_path = [path + suff for suff in (".model", ".weights")]
        if any(x is None or not exists(x) for x in (model_path, weights_path)):
            return None
        with open(model_path) as f:
            model = model_from_json(f.read())
        model.load_weights(weights_path)
        return model


class MLP(DNN):
    """Multi-label NN class"""
    name = "mlp"

    def __init__(self, config):
        self.config = config
        self.hidden = self.config.learner.hidden_dim
        self.layers = self.config.learner.num_layers
        DNN.__init__(self)

    def make(self):
        info("Building dnn: {}".format(self.name))
        DNN.make(self)
        self.input_shape = (self.input_dim,)

    # build MLP model
    def get_model(self):
        model = None
        model = Sequential()
        for i in range(self.layers):
            if i == 0:
                model.add(Dense(self.hidden, input_shape=self.input_shape))
                model.add(Activation('relu'))
                model.add(Dropout(0.3))
            else:
                model.add(Dense(self.hidden))
                model.add(Activation('relu'))
                model.add(Dropout(0.3))

        model = DNN.add_softmax(self, model)
        model.compile(loss='categorical_crossentropy', optimizer='adam', metrics=['accuracy'])
        return model


class LSTM(DNN):
    name = "lstm"

    def __init__(self, config):
        self.config = config
        self.hidden = self.config.learner.hidden_dim
        self.layers = self.config.learner.num_layers
        self.sequence_length = self.config.learner.sequence_length
        if self.sequence_length is None:
            error("Undefined learning sequence length, but required for {}.".format(self.name))
        DNN.__init__(self)

    # make network
    def make(self):
        info("Building dnn: {}".format(self.name))
        DNN.make(self)

        # non constant instance element num
        if not all(np.all(x==x[0]) for x in self.elements_per_instance):
            error("Unequal elements per instance encountered.")

        epi_train, epi_test = [x[0] for x in self.elements_per_instance]
        if epi_train != epi_test:
            error("Unequal elements per instance for train ({}) and test ({})".format(epi_train, epi_test))
        error("[{}] not compatible with unit instance indexes.".format(self.name), epi_train == 1)
        self.sequence_length = epi_train
        self.input_shape = (self.sequence_length, self.input_dim)

        # sequence length data / label matching
        if self.num_train != self.num_train_labels and (self.num_train != self.sequence_length * self.num_train_labels):
            error("Irreconcilable lengths of training data and labels: {}, {} with learning sequence length of {}.".
                  format(self.num_train, self.num_train_labels, self.sequence_length))
        if self.num_test != self.num_test_labels and (self.num_test != self.sequence_length * self.num_test_labels):
            error("Irreconcilable lengths of test data and labels: {}, {} with learning sequence length of {}.".
                  format(self.num_test, self.num_test_labels, self.sequence_length))

    # fetch sequence lstm fold data
    def get_fold_data(self, data, labels=None, data_idx=None, label_idx=None):
        # handle indexes by parent's function
        x, y = DNN.get_fold_data(self, data, labels, data_idx, label_idx)
        # reshape input data to num_docs x vec_dim x seq_len
        if not self.do_train_embeddings:
            x = np.reshape(x, (-1, self.sequence_length, self.input_dim))
        return x, y

    # preprocess input
    def process_input(self, data):
        return np.reshape(data, (-1, self.sequence_length, self.input_dim))

    # build the lstm model
    def get_model(self):
        model = Sequential()
        for i in range(self.layers):
            if self.layers == 1:
                # one and only layer
                model.add(keras_lstm(self.hidden, input_shape=self.input_shape))
            elif i == 0 and self.layers > 1:
                # first layer, more follow
                model.add(keras_lstm(self.hidden, input_shape=self.input_shape, return_sequences=True))
            elif i == self.layers - 1:
                # last layer
                model.add(keras_lstm(self.hidden))
            else:
                # intermmediate layer
                model.add(keras_lstm(self.hidden, return_sequences=True))
            model.add(Dropout(0.3))

        model = DNN.add_softmax(self, model)

        model.compile(loss='categorical_crossentropy',
                      optimizer='adam',
                      metrics=['accuracy'])

        # if self.config.is_debug():
        #     debug("Inputs: {}".format(model.inputs))
        #     model.summary()
        #     debug("Outputs: {}".format(model.outputs))
        return model

    # component functions
    def process_component_inputs(self):
        self.elements_per_instance = self.inputs.get_vectors(single=True).elements_per_instance
        super().process_component_inputs()

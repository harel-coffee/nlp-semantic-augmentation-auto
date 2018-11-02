import pickle
import os
import random

from sklearn import metrics
from sklearn.exceptions import UndefinedMetricWarning
from sklearn.model_selection import StratifiedKFold
import warnings
warnings.simplefilter(action='ignore', category=UndefinedMetricWarning)

import numpy as np
import pandas as pd

from keras.models import Sequential
from keras.layers import Activation, Dense, Dropout, Embedding, Reshape
from keras.layers import LSTM as keras_lstm
from keras.utils import to_categorical
from keras import callbacks
import gc

from utils import info, debug, tic, toc, error, write_pickled

class DNN:
    save_dir = "models"
    folds = None
    performance = {}
    cw_performance = {}
    run_types = ['random', 'majority', 'run']
    measures = ["precision", "recall", "f1-score", "accuracy"]
    classwise_aggregations = ["macro", "micro", "classwise", "weighted"]
    stats = ["mean", "var", "std", "folds"]
    sequence_length = None


    do_train_embeddings = False
    train_embeddings_params = []
    def create(config):
        name = config.learner.name
        if name == LSTM.name:
            return LSTM(config)
        elif name == MLP.name:
            return MLP(config)
        else:
            error("Undefined learner: {}".format(name))

    def __init__(self):
        info("Creating learner: {}".format(self.config.learner.to_str()))
        for run_type in self.run_types:
            self.performance[run_type] = {}
            for measure in self.measures:
                self.performance[run_type][measure] = {}
                for aggr in self.classwise_aggregations:
                    self.performance[run_type][measure][aggr] = {}
                    for stat in self.stats:
                        self.performance[run_type][measure][aggr][stat] = None
                    self.performance[run_type][measure][aggr]["folds"] = []
            # remove undefined combos
            for aggr in [x for x in self.classwise_aggregations if x not in ["macro", "classwise"]]:
                del self.performance[run_type]["accuracy"][aggr]

        # pritn only these, from config
        self.preferred_types = self.config.print.run_types if self.config.print.run_types else self.run_types
        self.preferred_measures = self.config.print.measures if self.config.print.measures else self.measures
        self.preferred_aggregations = self.config.print.aggregations if self.config.print.aggregations else self.classwise_aggregations
        self.preferred_stats = self.stats


    # aggregated evaluation measure function shortcuts
    def get_pre_rec_f1(self, preds, metric, gt=None):
        if gt is None:
            gt = self.test_labels
        cr = pd.DataFrame.from_dict(metrics.classification_report(gt, preds, output_dict=True))
        # classwise, micro, macro, weighted
        cw = cr.loc[metric].iloc[:-3].as_matrix()
        mi = cr.loc[metric].iloc[-3]
        ma = cr.loc[metric].iloc[-2]
        we = cr.loc[metric].iloc[-1]
        return cw, mi, ma, we

    def acc(self, preds, gt=None):
        if gt is None:
            gt = self.test_labels
        return metrics.accuracy_score(gt, preds)


    def cw_acc(self, preds, gt=None):
        if gt is None:
            gt = self.test_labels
        cm = metrics.confusion_matrix(gt, preds)
        cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
        return cm.diagonal()


    def check_embedding_training(self, model):
        if self.do_train_embeddings:
            model.add(Embedding(self.vocabulary_size + 1, self.embedding_dim, input_length = self.sequence_length))
        return model

    # define useful keras callbacks for the training process
    def get_callbacks(self, fold_index="x"):
        self.callbacks = []
        self.results_folder = self.config.folders.results
        models_folder = os.path.join(self.results_folder, "models")
        logs_folder = self.results_folder
        [os.makedirs(x, exist_ok=True) for x in  [self.results_folder, models_folder, logs_folder]]

        # model saving with early stoppingtch_si
        self.model_path = os.path.join(models_folder,"{}_fold_{}_".format(self.name, fold_index))
        weights_path = os.path.join(models_folder,"{}_fold_{}_".format(self.name, fold_index) + "ep_{epoch:02d}_valloss_{val_loss:.2f}.hdf5")
        self.model_saver = callbacks.ModelCheckpoint(weights_path, monitor='val_loss', verbose=0,
                                                   save_best_only=True, save_weights_only=False,
                                                   mode='auto', period=1)
        self.callbacks.append(self.model_saver)
        if self.early_stopping_patience:
            self.early_stopping = callbacks.EarlyStopping(monitor='val_loss', min_delta=0, patience=self.early_stopping_patience, verbose=0,
                                                      mode='auto', baseline=None, restore_best_weights=False)
            self.callbacks.append(self.early_stopping)

        # stop on NaN
        self.nan_terminator = callbacks.TerminateOnNaN()
        self.callbacks.append(self.nan_terminator)
        # learning rate modifier at loss function plateaus
        self.lr_reducer = callbacks.ReduceLROnPlateau(monitor='val_loss', factor=0.1,
                                                          patience=10, verbose=0, mode='auto',
                                                          min_delta=0.0001, cooldown=0, min_lr=0)
        self.callbacks.append(self.lr_reducer)
        # logging
        log_file = os.path.join(logs_folder,"{}_fold_{}.csv".format(self.name, fold_index))
        self.csv_logger = callbacks.CSVLogger(log_file, separator=',', append=False)
        self.callbacks.append(self.csv_logger)

        return self.callbacks

    def make(self, embedding, targets, num_labels):
        if embedding.base_name == "train":
            self.do_train_embeddings = True
            self.embedding_dim = embedding.get_dim()
            info("Will train {}-dimensional embeddings.".format(self.embedding_dim))
            self.final_dim = embedding.get_final_dim()
            self.vocabulary_size = embedding.get_vocabulary_size()
            emb_seqlen = embedding.sequence_length
            if self.sequence_length is not None:
                if emb_seqlen != self.sequence_length:
                    error("Specified embedding sequence of length {}, but learner sequence is of length {}".format(emb_seqlen, self.sequence_length))
            self.sequence_length = emb_seqlen

        self.verbosity = 1 if self.config.log_level == "debug" else 0
        self.train, self.test = embedding.get_data()
        self.num_labels = num_labels
        self.train_labels, self.test_labels = [np.asarray(x, np.int32) for x in targets]
        self.input_dim = embedding.get_final_dim()

        self.epochs = self.config.train.epochs
        self.folds = self.config.train.folds
        self.early_stopping_patience = self.config.train.early_stopping_patience
        self.seed = self.config.get_seed()
        self.batch_size = self.config.train.batch_size
        self.validation_portion = self.config.train.validation_portion

    def process_input(self, data):
        if self.do_train_embeddings:
            # reshape as per the sequence
            return np.reshape(data, (-1, self.sequence_length))
        return data

    def report_early_stopping(self):
        if self.early_stopping_patience:
            info("Stopped on epoch {}/{}".format(self.early_stopping.stopped_epoch+1, self.epochs))
            write_pickled(self.model_path + ".early_stopping", self.early_stopping.stopped_epoch)

    # train without cross-validation
    def train_model(self):
        tic()
        info("Training {} with input data: {} with a {} validation portion.".format(self.name, len(self.train), self.validation_portion))
        model = self.get_model()
        if self.config.is_debug():
            print("Inputs:", model.inputs)
            model.summary()
            print("Outputs:", model.outputs)

        train_y_onehot = to_categorical(self.train_labels, num_classes = self.num_labels)

        # shape accordingly
        self.train = self.process_input(self.train)
        history = model.fit(self.train, train_y_onehot,
                            batch_size=self.batch_size,
                            epochs=self.epochs,
                            verbose = self.verbosity,
                            validation_split= self.validation_portion,
                            callbacks = self.get_callbacks())

        self.report_early_stopping()
        self.do_test(model, print_results=True)
        toc("Training")
        return model

    def do_traintest(self):
        if self.folds > 1:
            self.train_model_crossval()
        else:
            self.train_model()


    # train with cross-validation folds
    def train_model_crossval(self):
        tic()
        info("Training {} with input data: {} on {} stratified folds".format(self.name, len(self.train), self.folds))

        fold_data = self.get_fold_indexes()
        for fold_index, (train_d_idx, train_l_idx, val_d_idx, val_l_idx) in enumerate(fold_data):
            tic()
            train_x, train_y = self.get_fold_data(self.train, self.train_labels, train_d_idx, train_l_idx)
            val_x, val_y = self.get_fold_data(self.train, self.train_labels, val_d_idx, val_l_idx)
            # convert labels to one-hot
            train_y_onehot = to_categorical(train_y, num_classes = self.num_labels)
            val_y_onehot = to_categorical(val_y, num_classes = self.num_labels)

            # train
            gc.collect()
            model = self.get_model()
            if self.config.is_debug():
                print("Inputs:", model.inputs)
                model.summary()
                print("Outputs:", model.outputs)
            #print(val_x, val_y)
            info("Trainig fold {}/{}".format(fold_index + 1, self.folds))
            history = model.fit(train_x, train_y_onehot,
                                batch_size=self.batch_size,
                                epochs=self.epochs,
                                validation_data = (val_x, val_y_onehot),
                                verbose = self.verbosity,
                                callbacks = self.get_callbacks(fold_index))

            self.report_early_stopping()
            self.do_test(model, print_results=self.config.print.folds, fold_index=fold_index)
            toc("Fold #{}/{} training/testing".format(fold_index+1, self.folds))
        toc("Total training/testing")
        # report results across folds
        self.report_across_folds()

    # print performance across folds
    def report_across_folds(self):
        info("==============================")
        info("Mean / var / std performance across all {} folds:".format(self.folds))
        for type in self.run_types:
            for measure in self.measures:
                for aggr in self.classwise_aggregations:
                    if aggr not in self.performance[type][measure] or aggr == "classwise":
                        continue
                    container = self.performance[type][measure][aggr]
                    if not container:
                        continue
                    #print(type, measure, aggr, container)
                    mean_perf = np.mean(container["folds"])
                    var_perf = np.var(container["folds"])
                    std_perf = np.std(container["folds"])
                    # print, if it's prefered
                    if all([ type in self.preferred_types, measure in self.preferred_measures, aggr in self.preferred_aggregations]):
                        info("{:10} {:10} {:10} : {:.3f} {:.3f} {:.3f}".format(type, aggr, measure, mean_perf, var_perf, std_perf))
                    # add fold-aggregating performance information
                    self.performance[type][measure][aggr]["mean"] = mean_perf
                    self.performance[type][measure][aggr]["var"] = var_perf
                    self.performance[type][measure][aggr]["std"] = std_perf
        # write the results in csv in the results directory
        # entries in a run_type - measure configuration list are the foldwise scores, followed by the mean
        df = pd.DataFrame.from_dict(self.performance)
        df.to_csv(os.path.join(self.results_folder, "results.txt"))
        with open(os.path.join(self.results_folder, "results.pickle"), "wb") as f:
            pickle.dump(df, f)


    def do_test(self, model, print_results=False, fold_index=0):
        if self.folds > 1:
            test_data, _ = self.get_fold_data(self.test)
        else:
            test_data = self.process_input(self.test)
        predictions = model.predict(test_data, batch_size=self.batch_size, verbose=self.verbosity)
        predictions_amax = np.argmax(predictions, axis=1)
        # get baseline performances
        self.compute_performance(predictions_amax)
        if print_results:
            info("Test results:")
            self.print_performance(fold_index)

    # get fold data
    def get_fold_indexes_sequence(self):
        idxs = []
        skf = StratifiedKFold(self.folds, shuffle=False, random_state=self.seed)
        # get first-vector positions
        data_full_index = np.asarray((range(len(self.train))))
        single_vector_data = list(range(0, len(self.train), self.sequence_length))
        fold_data = list(skf.split(single_vector_data, self.train_labels))
        for train_test in fold_data:
            # get train indexes
            train_fold_singlevec_index, test_fold_singlevec_index = train_test
            # transform to full-sequence indexes
            train_fold_index = data_full_index[train_fold_singlevec_index]
            test_fold_index = data_full_index[test_fold_singlevec_index]
            # expand to the rest of the sequence members
            train_fold_index = [j for i in train_fold_index for j in list(range(i, i + self.sequence_length))]
            test_fold_index = [j for i in test_fold_index for j in list(range(i, i + self.sequence_length))]
            idxs.append((train_fold_index, train_fold_singlevec_index, test_fold_index, test_fold_singlevec_index))
        return idxs

    # fold generator function
    def get_fold_indexes(self):
        if len(self.train) != len(self.train_labels):
            # multi-vector samples
            return self.get_fold_indexes_sequence()
        else:
            skf = StratifiedKFold(self.folds, shuffle=False, random_state = self.seed)
            return [(train, train, val, val) for (train, val) in skf.split(self.train, self.train_labels)]

    # data preprocessing function
    def get_fold_data(self, data, labels=None, data_idx=None, label_idx=None):
        # if indexes provided, take only these parts
        if data_idx is not None:
            x = data[data_idx]
        else:
            x = data
        if labels is not None:
            if label_idx is not None:
                y = labels[label_idx]
            else:
                y = labels
        else:
            y = None
        return x, y

    # add softmax classification layer
    def add_softmax(self, model, is_first=False):
        if is_first:
            model.add(Dense(self.num_labels, input_shape=self.input_shape, name="dense_classifier"))
        else:
            model.add(Dense(self.num_labels, name="dense_classifier"))

        model.add(Activation('softmax', name="softmax"))
        return model

    # compute classification baselines
    def compute_performance(self, predictions):
        # add run performance
        self.add_performance("run", predictions)
        maxfreq, maxlabel = -1, -1
        for t in set(self.test_labels):
            freq = len([1 for x in self.test_labels if x == t])
            if freq > maxfreq:
                maxfreq = freq
                maxlabel = t

        majpred = np.repeat(maxlabel, len(self.test_labels))
        self.add_performance("majority", majpred)
        randpred = np.asarray([random.choice(list(range(self.num_labels))) for _ in self.test_labels], np.int32)
        self.add_performance("random", randpred)

    # compute scores and append to per-fold lists
    def add_performance(self, type, preds):
        # get accuracies
        acc, cw_acc = self.acc(preds), self.cw_acc(preds)
        self.performance[type]["accuracy"]["classwise"]["folds"].append(cw_acc)
        self.performance[type]["accuracy"]["macro"]["folds"].append(acc)
        # self.performance[type]["accuracy"]["micro"].append(np.nan)
        # self.performance[type]["accuracy"]["weighted"].append(np.nan)

        # get everything else
        for measure in [x for x in self.measures if x !="accuracy"]:
            cw, ma, mi, ws = self.get_pre_rec_f1(preds, measure)
            self.performance[type][measure]["classwise"]["folds"].append(cw)
            self.performance[type][measure]["macro"]["folds"].append(ma)
            self.performance[type][measure]["micro"]["folds"].append(mi)
            self.performance[type][measure]["weighted"]["folds"].append(ws)


    # print performance of the latest run
    def print_performance(self, fold_index=0):
        info("---------------")
        for type in self.preferred_types:
            info("{} performance:".format(type))
            for measure in self.preferred_measures:
                for aggr in self.preferred_aggregations:
                    # don't print classwise results or unedfined aggregations
                    if aggr not in self.performance[type][measure] or aggr == "classwise":
                        continue
                    container = self.performance[type][measure][aggr]
                    if not container:
                        continue
                    info('{} {}: {:.3f}'.format(aggr, measure, self.performance[type][measure][aggr]["folds"][fold_index]))

class MLP(DNN):
    name = "mlp"
    def __init__(self, config):
        self.config = config
        self.hidden = self.config.learner.hidden_dim
        self.layers = self.config.learner.num_layers
        self.sequence_length = self.config.learner.sequence_length
        DNN.__init__(self)


    def check_embedding_training(self, model):
        if self.do_train_embeddings:
            error("Embedding training unsupported for {}".format(self.name))
            model = DNN.check_embedding_training(self, model)
            # vectorize
            model.add(Reshape(target_shape=(-1, self.embedding_dim)))
        return model

    def make(self, embeddings, targets, num_labels):
        info("Building dnn: {}".format(self.name))
        DNN.make(self, embeddings, targets, num_labels)
        self.input_shape = (self.input_dim,)
        aggr = self.config.embedding.aggregation
        aggregation = aggr[0]
        if aggregation not in ["avg"] and not self.do_train_embeddings:
            error("Aggregation {} incompatible with {} model.".format(aggregation, self.name))
        if embeddings.name == "train":
            error("{} cannot be used to train embeddings.".format(self.name))

    # build MLP model
    def get_model(self):
        model = None
        model = Sequential()
        model = self.check_embedding_training(model)
        for i in range(self.layers):
            if i == 0 and not self.do_train_embeddings:
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
            error("Undefined learner sequence length, but required for {}.".format(self.name))
        DNN.__init__(self)

    # make network
    def make(self, embeddings, targets, num_labels):
        info("Building dnn: {}".format(self.name))
        DNN.make(self, embeddings, targets, num_labels)
        # make sure embedding aggregation is compatible
        # with the sequence-based lstm model
        self.input_shape = (self.sequence_length, self.input_dim)
        aggr = self.config.embedding.aggregation
        aggregation = aggr[0]
        if aggregation not in ["pad"]:
            error("Aggregation {} incompatible with {} model.".format(aggregation, self.name))
        if aggr in ["train"]:
            error("Embedding {} incompatible with {} model.".format(aggregation, self.name))



    # fetch sequence lstm fold data
    def get_fold_data(self, data, labels=None, data_idx=None, label_idx=None):
        # handle indexes by parent's function
        x, y = DNN.get_fold_data(self, data, labels, data_idx, label_idx)
        # reshape input data to num_docs x vec_dim x seq_len
        if not self.do_train_embeddings:
            x = np.reshape(x, (-1, self.sequence_length, self.input_dim))
        else:
            x = np.reshape(x, (-1, self.sequence_length))
            # replicate labels to match each input
            # y = np.stack([y for _ in range(self.sequence_length)])
            # y = np.reshape(np.transpose(y), (-1,1))
            pass
        return x, y

    # preprocess input
    def process_input(self, data):
        if self.do_train_embeddings:
            return DNN.process_input(self, data)
        return np.reshape(data, (-1, self.sequence_length, self.input_dim))

    # build the lstm model
    def get_model(self):
        model = Sequential()
        model = self.check_embedding_training(model)
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
        return model


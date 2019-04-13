from learner import Learner
from utils import tictoc, write_pickled, info, error, read_pickled, one_hot
import numpy as np
from sklearn.naive_bayes import GaussianNB


class Classifier(Learner):

    def __init__(self):
        """Generic classifier constructor
        """
        Learner.__init__(self)

    def make(self, representation, dataset):
        Learner.make(self, representation, dataset)


class SKLClassifier(Classifier):
    """Scikit-learn classifier"""
    def __init__(self):
        Classifier.__init__(self)

    def make(self, representation, dataset):
        Classifier.make(self, representation, dataset)

    # split train/val labels, do *not* convert to one-hot
    def prepare_labels(self, trainval_idx):
        train_idx, val_idx = trainval_idx
        train_labels = self.train_labels
        if len(train_idx) > 0:
            train_labels = [self.train_labels[i] for i in train_idx]
        else:
            train_labels = np.empty((0,))
        if len(val_idx) > 0:
            val_labels = [self.train_labels[i] for i in val_idx]
        else:
            val_labels = np.empty((0,))
        return train_labels, val_labels

    def train_model(self, trainval_idx):
        model = self.model()
        train_data, train_labels, _ = self.get_trainval_data(trainval_idx)
        model.fit(train_data, np.asarray(train_labels).ravel())
        return model

    # evaluate a clustering
    def test_model(self, test_data, model):
        predictions = model.predict(test_data)
        # convert back to one-hot
        predictions = one_hot(predictions, self.num_labels)
        return predictions

class NaiveBayes(SKLClassifier):
    name = "naive_bayes"

    def __init__(self, config):
        self.config = config
        self.model = GaussianNB
        SKLClassifier.__init__(self)

    def make(self, representation, dataset):
        if dataset.is_multilabel():
            error("Cannot apply {} to multilabel data.".format(self.name))
        SKLClassifier.make(self, representation, dataset)


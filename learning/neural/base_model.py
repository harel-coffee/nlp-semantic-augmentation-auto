import torch
from torch.nn import functional as F
import pytorch_lightning as ptl
from utils import error, info

from pytorch_lightning import Trainer
from pytorch_lightning.callbacks.progress import ProgressBar
from torch.utils.data import DataLoader, RandomSampler

class BaseModel(ptl.LightningModule):
    """Base class for pytorch models, organized as a pytorch-lightning module

    This class and its derivatives must implement
    a) all functionality required by pytorch nn modules  (forward, etc.)
    b) all functionality required by pytorch-lightning modules (train_dataloader, configure_optimizers, etc.)
    """
    config = None
    name = "BASE_MODEL"

    def __init__(self, config, wrapper_name, working_folder, model_name):
        """Model constructor"""
        self.config = config
        self.name = self.config.name
        self.wrapper_name = wrapper_name
        self.working_folder = working_folder
        self.model_name = model_name
        self.callbacks = []
        super(BaseModel, self).__init__()

    class SmaugProgressBar(ProgressBar):
        def on_epoch_start(trainer, pl_module):
            print()
            super().on_epoch_start(trainer, pl_module)

    class Dataset:
        """Dataset class to construct the required dataloaders"""
        def __init__(self, data, labels=None):
            self.data = data
            self.labels = labels

        def __len__(self):
            return len(self.data)

        def __getitem__(self, index):
            datum = self.data[index]
            if self.labels is not None:
                return datum, self.labels[index]
            return datum

    def configure_embedding(self):
        # incorporate embeddings in the neural architecture
        error("Attempted to access abstract embedding configuration function.")

    # high-level functions for NN ptl operations
    ########################################
    def train_model(self):
        """Training and validation function"""
        # also check https://pytorch-lightning.readthedocs.io/en/latest/fast_training.html
        logger = ptl.loggers.TensorBoardLogger(self.working_folder, name=self.model_name)

        # trainer = Trainer(val_check_interval=100)
        # self.callbacks.append(BaseModel.SmaugProgressBar())
        # trainer = Trainer(logger=logger, min_epochs=1, max_epochs=self.config.train.epochs, callbacks=self.callbacks)
        trainer = Trainer(logger=logger, min_epochs=1, max_epochs=self.config.train.epochs)
        trainer.fit(self)

    def test_model(self):
        """Testing function

        Since we are interested in the model predictions and make the evaluation outside ptl, this should never be required to run.
        """
        error("Attempted to invoke test_model form within a ptl module -- a test_model() should be run in the wrapper class instead that just invokes forward().")
        # trainer = Trainer()
        # trainer.test(self)

    # low-level functions for NN ptl steps
    # ####################################
    # forward fn -- should be defined in a subclass
    def forward(self, x):
        """Forward pass function"""
        # forward pass
        error("Attempted to access abstract forward function")

    def get_data_from_index(self, index, data):
        """Retrieve a subset of embedding data"""
        return data[index]

    def should_do_validation(self):
        try:
            return len(self.val_index) > 0
        except TypeError:
            return False

    # def get_data(self, index):
    #     return self.embeddings[index]

    # dataset / dataloader utils
    def make_dataset_from_index(self, index, labels):
        # data = self.get_data(index)
        return BaseModel.Dataset(index, labels)

    def prepare_data(self):
        """Preparatory data actions and/or writing to disk"""
        pass

    def train_dataloader(self):
        """Preparatory actions for training data"""
        self.train_dataset = self.make_dataset_from_index(self.train_index, self.train_labels)
        return DataLoader(self.train_dataset, self.config.train.batch_size, num_workers=6, sampler=RandomSampler(self.train_dataset))

    def val_dataloader(self):
        """Preparatory transformation actions for validation data"""
        if self.should_do_validation():
            self.val_dataset = self.make_dataset_from_index(self.val_index, self.val_labels)
            return DataLoader(self.val_dataset, self.config.train.batch_size, num_workers=6, sampler=RandomSampler(self.val_dataset))
        return None

    def test_dataloader(self):
        """Preparatory transformation actions for test data"""
        # self.test_dataset = self.make_dataset_from_index(self.test_index, self.test_labels)
        self.test_dataset = self.make_dataset_from_index(self.test_index, None)
        return DataLoader(self.test_dataset, self.config.train.batch_size, shuffle=False, num_workers=6)

    # training
    def configure_optimizers(self):
        """Setup optimizers for training"""
        # optimizers
        if self.config.train.optimizer == "adam":
            optim = torch.optim.Adam(self.parameters())
        elif self.config.train.optimizer == "sgd":
            optim = torch.optim.SGD(self.parameters(), lr=self.config.train.base_lr)
        info(f"Training with optimizer {optim}")
        # LR Scheduler
        if self.config.train.lr_scheduler is not None:
            if self.config.train.lr_scheduler == "plateau":
                scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optim)
            error(f"Undefined lr_scheduler: {self.config.train.lr_scheduler}")
            info(f"Using LR scheduling: {scheduler}")
            return [optim], [scheduler]
        return optim

    def account_for_padding(self, logits, y):
        # account for mismatches produced by padding in the input
        # => truncate the logits to the ground truth size
        if len(logits) != len(y):
            logits = logits[:len(y)]
        return logits

    def training_step(self, batch, batch_idx):
        """Define a single training step"""
        x, y = batch
        logits = self.forward(x)
        logits = self.account_for_padding(logits, y)

        loss = F.cross_entropy(logits, y)

        # add logging
        logs = {'loss': loss}
        return {'loss': loss, 'log': logs}

    # validation
    def validation_step(self, batch, batch_idx):
        """Define a single validation step"""
        x, y = batch
        logits = self.forward(x)
        logits = self.account_for_padding(logits, y)
        loss = F.nll_loss(logits, y)
        loss_dict = {'val_loss': loss}
        return {'val_loss': loss, 'log': loss_dict}

        # error("Attempted to access abstract validation step function")
    def validation_epoch_end(self, outputs):
        """Define metric computation at validation epoch end"""
        if self.should_do_validation():
            avg_loss = torch.stack([x['val_loss'] for x in outputs]).mean()
            tensorboard_logs = {'val_loss': avg_loss}
            return {'avg_val_loss': avg_loss, 'log': tensorboard_logs}
        return {}

    def test_step(self, batch, batch_idx):
        """Define a single testing step"""
        x, y = batch
        logits = self.forward(x)
        logits = self.account_for_padding(logits, y)
        loss = F.nll_loss(logits, y)
        loss_dict = {'test_loss': loss}
        return {'test_loss': loss, 'log': loss_dict}

    def test_epoch_end(self, outputs):
        """Define metric computation at test epoch end"""
        avg_loss = torch.stack([x['val_loss'] for x in outputs]).mean()
        tensorboard_logs = {'test_loss': avg_loss}
        return {'avg_test_loss': avg_loss, 'log': tensorboard_logs}
        # error("Attempted to access abstract test epoch end function")

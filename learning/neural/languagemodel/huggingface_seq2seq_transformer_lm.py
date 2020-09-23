"""Module for the incorporation of transformer models from huggingface"""
import numpy as np

import defs
import torch
from utils import error, info, one_hot, equal_lengths, warning, shapes_list
from learning.neural.languagemodel.huggingface_transformer_lm import HuggingfaceTransformerLanguageModel
from bundle.datatypes import *
from bundle.datausages import *

from torch.utils.data import DataLoader
from learning.neural.models import instantiator
from os.path import exists, dirname

class HuggingfaceSeq2SeqTransformerLanguageModel(HuggingfaceTransformerLanguageModel):
    """Wrapper class for seq2seqhuggingface transformer models"""

    name = "huggingface_seq2seq_transformer_lm"

    def __init__(self, config):
        """
        Keyword Arguments:
        config -- Configuration object
        """
        self.config = config
        HuggingfaceTransformerLanguageModel.__init__(self, config)


    # def fetch_language_model_inputs(self):
    #     # obtain regular texts
    #     super().fetch_language_model_inputs()
    #     # obtain target texts as well
          # MOVED TO SUP. LEARNER
    #     # number of index groups have to match
    #     error("Unequal indices for input and target texts", not equal_lengths(self.indices.instances, self.target_indices.instances))
    def get_ground_truth(self):
        """Ground truth retrieval function"""
        # fetch the gt textual gt token embeddings
        return self.target_embeddings, self.target_masks

    def map_text(self):
        """Process input text into tokenized elements"""
        # map regular inputs
        super().map_text()
        # map targets
        info("Tokenizing seq2seq LM textual ground truth data to tokens")
        self.target_embeddings, self.target_masks, self.target_train_embedding_index, self.target_test_embedding_index  = self.map_text_collection(self.targets, self.target_indices)

        # check correspondence with inputs
        checks = [((self.target_embeddings, self.embeddings, self.masks, self.target_masks), "embeddings and masks"),
                  ((self.train_embedding_index, self.target_train_embedding_index), "train indexes"),
                  ((self.test_embedding_index, self.target_test_embedding_index), "test indexes")]
        error_exists = False
        for ch in checks:
            if not equal_lengths(ch[0]):
                warning(ch[1] + "shapes:" + shapes_list(ch[0]))
                error_exists = True
            error("Inconsistent inputs / targets mapping outputs:", error_exists)




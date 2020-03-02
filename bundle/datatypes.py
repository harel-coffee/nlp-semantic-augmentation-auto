"""Module defining bundle datatypes"""

import numpy as np

import defs
from defs import avail_roles, roles_compatible
from utils import error


class Text:
    name = "text"
    instances = None
    vocabulary = None

    def __init__(self, inst, vocab=None):
        self.instances = inst
        self.vocabulary = vocab


class Vectors:
    name = "vectors"
    instances = None
    elements_per_instance = None

    def __init__(self, vecs, epi=None):
        self.instances = vecs
        if epi is None:
            try:
                epi = [np.ones(len(x)) for x in self.instances]
            except:
                epi = [len(self.instances)]
        self.elements_per_instance = epi

    def get_instances(self):
        return self.instances


class Labels:
    name = "labels"
    instances = None
    labelset = None
    is_multilabel = None

    def __init__(self, labels, labelset, multilabel):
        self.instances = labels
        self.labelset = labelset
        self.multilabel = multilabel


class Indices:
    name = "indices"
    instances = None
    roles = None

    def __init__(self, indices, roles=None):
        self.instances = indices
        if roles is not None:
            error(f"Undefined role(s): {roles}", any(x not in avail_roles for x in roles))
            error(f"Mismatch role / indices lengths: {len(roles)} {len(indices)}", len(roles) != len(indices))
            # error(f"Multiple-role incompatibility: {roles}", not roles_compatible(roles))
        self.roles = roles

    def has_role(self, role):
        """Checks for the existence of a role"""
        if not self.roles:
            return False
        return role in self.roles

    def get_train_role_indexes(self):
        """Retrieve instance indexes with a training role"""
        res = []
        for r, role in enumerate(self.roles):
            if role == defs.roles.train:
                res.append(r)
        return res

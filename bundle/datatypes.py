"""Module defining bundle datatypes"""

import numpy as np

import defs
from defs import avail_roles, roles_compatible
from utils import error


class Datatype:
    roles = None
    instances = None
    def __init__(self, instances, roles=None):
        if roles is not None:
            error(f"Undefined role(s): {roles}", any(x not in avail_roles for x in roles))
            error(f"Mismatch role / instances lengths: {len(roles)} {len(instances)}", len(roles) != len(instances))
        self.roles = roles
        self.instances = instances

    def get_instance_index(self, idx):
        llen = len(self.instances)
        if llen <= idx:
            error(f"Requested index {idx} from {self.name} datatype which has only (llen)")
        return self.instances[idx]

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


class Text(Datatype):
    name = "text"
    vocabulary = None

    def __init__(self, inst, vocab=None, roles=None):
        super().__init__(inst, roles)
        self.vocabulary = vocab


class Vectors(Datatype):
    name = "vectors"
    elements_per_instance = None

    def __init__(self, vecs, epi=None, roles=None):
        super().__init__(vecs, roles)
        if epi is None:
            try:
                epi = [np.ones(len(x)) for x in self.instances]
            except:
                epi = [len(self.instances)]
        self.elements_per_instance = epi


class Labels(Datatype):
    name = "labels"
    labelset = None
    is_multilabel = None

    def __init__(self, labels, labelset, multilabel, roles=None):
        super().__init__(labels, roles)
        self.labelset = labelset
        self.multilabel = multilabel


class Indices(Datatype):
    name = "indices"

    def __init__(self, indices, roles=None):
        super().__init__(indices, roles)
    def summarize_content(self):
        """Print a summary of the data"""
        return f"{self.name} {len(self.instances)} instances, {self.roles} roles"

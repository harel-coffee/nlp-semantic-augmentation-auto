from utils import error, read_pickled, info, debug, write_pickled
from component.component import Component
from os.path import exists, isfile, join, dirname, isabs
from os import makedirs

"""
Class to represent an object loadable from disk or computable,
used in the classification pipeline. It can have three successive states:
- raw: a format that requires specific object-dependent loading
- serialized: a serialized format with pickle
- preprocessed: a serialized format with pickle, directly usable in the next pipeline phase

raw formats should yield serialized object versions,
and applying preprocessing on the serialized object
should produce an object directly usable to the next
phase of the pipeline.

The raw, serialized and preprocessed object names (i.e. loadable file names)
are computed automatically from a base object name.

"""


class Serializable(Component):
    name = None
    base_name = None
    serialization_dir = None
    loaded_raw = False

    # flags for data loading
    loaded_aggregated = False
    loaded_raw_serialized = False
    loaded_preprocessed = False

    # variables for serialization paths
    serialization_path_aggregated = None
    serialization_path_preprocessed = None
    serialization_path = None

    # paths from where to load data, in priority order
    data_paths = []
    # corresponding read functions to acquire the data
    read_functions = []
    # corresponding processing functions to call on loaded data
    handler_functions = []
    # load flags
    load_flags = []

    # paths to load necessary resources required to compute data from scratch
    resource_paths = []
    # corresponding reader and hanlder functions
    resource_read_functions = []
    resource_handler_functions = []
    resource_always_load_flag = []

    successfully_loaded_path = None

    def __init__(self, dir_name):
        self.serialization_dir = join(self.config.folders.serialization, dir_name)
        self.raw_data_dir = join(self.config.folders.raw_data, dir_name)
        self.loaded_raw_serialized = False
        self.loaded_serialized = False
        self.loaded_preprocessed = False
        self.loaded_aggregated = False
        self.multiple_config_names = None
        self.deserialization_allowed = self.config.misc.allow_deserialization
        self.set_multiple_config_names()

    def loaded(self):
        return any(self.load_flags)

    def set_multiple_config_names(self):
        pass

    def populate(self):
        error("Attempted to access abstract populate function of {}.".format(self.name))

    def load_any_of(self, path_names):
        for s, name in enumerate(path_names):
            # debug("Attempting to load semantic info from source {}/{}: {}".format(s + 1, len(config_names), semantic_name))
            self.name = name
            self.set_serialization_params()
            # add extras
            self.set_additional_serialization_sources()
            self.set_resources()
            self.successfully_loaded_path = name
            self.load_single_config_data()
            if self.loaded():
                info("Loaded {} info by using name: {}".format(self.name, name))
                return True
        return False


    def add_serialization_source(self, path, reader=read_pickled, handler=lambda x: x):
        self.data_paths.insert(0, path)
        self.read_functions.insert(0, reader)
        self.handler_functions.insert(0, handler)

    def set_serialization_params(self):
        self.data_paths = []
        self.read_functions = []
        self.handler_functions = []
        self.load_flags = []
        # setup paths
        self.configure_serialization_paths()
        # alias some paths
        self.read_functions = [read_pickled, read_pickled, self.fetch_raw]
        self.handler_functions = [self.handle_preprocessed, self.handle_raw_serialized, self.handle_raw]

    # set paths according to serializable name
    def get_paths_by_name(self, name=None, raw_path=None):
        if name is None:
            name = self.name
        if not exists(self.serialization_dir):
            makedirs(self.serialization_dir, exist_ok=True)
        # raw
        serialization_path = "{}/{}_raw.pkl".format(self.serialization_dir, name)
        # preprocessed
        serialization_path_preprocessed = "{}/{}.preprocessed.pkl".format(self.serialization_dir, name)
        return [serialization_path_preprocessed, serialization_path, raw_path]

    def configure_serialization_paths(self):
        self.data_paths = self.get_paths_by_name(self.name, raw_path=self.get_raw_path())
        self.serialization_path_preprocessed, self.serialization_path = self.data_paths[:2]

    def set_additional_serialization_sources(self):
        pass

    # attemp to load resource from specified paths
    def attempt_load(self, index):
        path, reader, handler = [x[index] for x in [self.data_paths, self.read_functions, self.handler_functions]]

        # either path is None (resource is acquired without one) or it's a file that will be loaded
        if path is None or (exists(path) and isfile(path)):
            debug("Attempting load of {} with {}.".format(path, self.read_functions[index]))
            data = reader(path)
            if data is None:
                # debug("Failed to load {} from path {}".format(self.name, path))
                return False
            # debug("Reading path {} with func {} and handler {}".format(path, reader, handler))
            self.successfully_loaded_path = path
            handler(data)
            self.load_flags[index] = True
            return True
        else:
            # debug("Failed to load {} from path {}".format(self.name, path))
            return False

    def acquire_resources(self):
        # Check if there are any required resources to load
        if self.resource_paths:
            for r, res in enumerate(self.resource_paths):
                info("Loading required resource {}/{}: {}".format(r + 1, len(self.resource_paths), res))
                read_result = self.resource_read_functions[r](res)
                self.resource_handler_functions[r](read_result)

    def acquire_data(self):
        if self.multiple_config_names is not None:
            return self.load_any_of(self.multiple_config_names)
        return self.load_single_config_data()

    def load_single_config_data(self):
        self.load_flags = [False for _ in self.data_paths]
        for index in range(len(self.data_paths)):
            if not self.deserialization_allowed and index < len(self.data_paths) - 1:
                debug("Skipping deser. of {} since it's not allowed".format(self.data_paths[index]))
                continue
            if (self.attempt_load(index)):
                return index
        # no data was found to load
        if not self.loaded():
            info("Failed to load {}".format(self.name))
            self.acquire_resources()
            return False
        return True

    # configure resources to load
    def set_resources(self):
        self.resource_paths = []
        self.resource_read_functions = []
        self.resource_handler_functions = []
        self.resource_always_load_flag = []

    def get_raw_path(self):
        return None

    def handle_preprocessed(self, preprocessed):
        error("Need to override preprocessed handling for {}".format(self.name))

    def set_raw_path(self):
        error("Need to override raw path dataset setter for {}".format(self.name))

    def fetch_raw(self, dummy_input):
        return None

    def handle_raw(self, raw_data):
        error("Need to override raw data handler for {}".format(self.name))

    def handle_raw_serialized(self, raw_serialized):
        error("Need to override raw serialized data handler for {}".format(self.name))

    def preprocess(self):
        error("Need to override preprocessing function for {}".format(self.name))

    def get_all_raw(self):
        error("Need to override raw data getter for {}".format(self.name))

    def get_all_preprocessed(self):
        error("Need to override preprocessed data getter for {}".format(self.name))

    def get_model_path(self):
        """Get a path of the trained model"""
        if self.config.explicit_model_path is not None:
            path = self.config.explicit_model_path
            info(f"Using explicit path {path}")
            if not exists(path):
                fp = join(self.config.folders.run, path)
                if exists(fp):
                    path = fp
                    info(f"Using full path of {path}")
            return path
        path = join(self.config.folders.run, self.name)
        return path

    def load_model(self):
        """Default model loading function, via pickled object deserializaLoad the model"""
        try:
            # info(f"Loading model for {self.get_full_name()}")
            self.model = read_pickled(self.get_model_path(), msg=f"{self.get_full_name()} model")
            return True
        except FileNotFoundError:
            return False

    def save_model(self):
        """Save the serializable model"""
        # write intermmediate folders
        makedirs(dirname(self.get_model_path()), exist_ok=True)
        write_pickled(self.get_model_path(), self.get_model(), msg=f"{self.get_full_name()} model" )

    def save_outputs(self):
        """Save the produced outputs"""
        info("Writing outputs to {}".format(self.serialization_path_preprocessed))
        write_pickled(self.serialization_path_preprocessed, self.get_all_preprocessed(), msg=f"{self.get_full_name()} outputs")

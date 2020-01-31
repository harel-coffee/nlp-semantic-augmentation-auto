"""Module for defining global-level configuration settings and components
"""
import logging
from os.path import join

from config.config import Configuration


class GlobalConfig(Configuration):
    """Global configuration class
    """
    # global-level configuration bundles
    misc = None
    print = None
    folders = None
    # key for declaring chains
    chains_key = "chains"

    def __init__(self):
        super().__init__(None)

    # logging initialization
    def setup_logging(self):
        formatter = logging.Formatter(fmt='%(asctime)s - %(levelname)7s | %(message)s')

        # console handler
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)

        lvl = logging._nameToLevel[self.print.log_level.upper()]
        # logger = logging.getLogger(self.logger_name)
        logger = logging.getLogger()
        logger.setLevel(lvl)
        logger.addHandler(handler)

        # file handler
        self.logfile = join(self.folders.run, "log_{}.log".format(self.misc.run_id))
        fhandler = logging.FileHandler(self.logfile)
        fhandler.setLevel(lvl)
        fhandler.setFormatter(formatter)
        logger.addHandler(fhandler)

        self.logger = logger
        return logger

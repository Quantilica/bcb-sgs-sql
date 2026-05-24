import logging
from importlib.metadata import PackageNotFoundError, version

from . import config, database, loader, sgs

try:
    __version__ = version("bcb-sgs-sql")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["config", "database", "loader", "sgs"]

logging.getLogger(__name__).addHandler(logging.NullHandler())

config.setup_logging(__name__, "bcb-sgs-sql.log")

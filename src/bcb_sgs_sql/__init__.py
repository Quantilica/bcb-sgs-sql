import logging
from importlib.metadata import PackageNotFoundError, version

from . import config, database, loader, sgs

try:
    __version__ = version("bcb-sgs-sql")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["config", "database", "loader", "sgs"]

# Library import must not have side effects: attach only a NullHandler and
# let the caller configure handlers. The CLI configures file/rich logging in
# ``cli.main()`` (via ``config.setup_logging``); non-CLI users configure their
# own logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())

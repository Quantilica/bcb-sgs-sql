"""Storage repository for BCB SGS data and metadata files.

Centralizes filesystem operations for the fetch-and-load path (Via A):
constructing deterministic file paths from a ``series_id`` and a
modification stamp, checking the cache, and reading/writing JSON.

Layout under ``data_dir``::

    data/series-{id:06d}@{stamp}.json     # observations (one file per run)
    metadata/{id:06d}_basic.json          # SeriesMetadataBasic (as dict)
    metadata/{id:06d}_full.json           # SeriesMetadataFull (as dict)

The ``metadata/{id:06d}_{basic,full}.json`` names match the convention
used by ``bcb-sgs-fetcher`` so caches written by either tool interoperate.
"""

import logging
from pathlib import Path

import orjson

from .config import Config

logger = logging.getLogger(__name__)

_SERIES_DIR = "data"
_METADATA_DIR = "metadata"


class Storage:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)

    @classmethod
    def default(cls, config: Config) -> "Storage":
        """Create a Storage rooted at the data directory from config."""
        data_dir = config.data_dir
        data_dir.mkdir(exist_ok=True, parents=True)
        return cls(data_dir)

    # -- observations ----------------------------------------------------

    @staticmethod
    def build_series_filename(series_id: int, stamp: str) -> str:
        """Build a deterministic JSON filename for a series + stamp.

        The filename ends with ``@{stamp}.json`` so that re-fetches with a
        newer stamp produce distinct files and :meth:`read_series_dir`
        keeps only the latest per series.
        """
        return f"series-{int(series_id):06d}@{stamp}.json"

    def get_series_filepath(self, series_id: int, stamp: str) -> Path:
        filename = self.build_series_filename(series_id, stamp)
        return self.data_dir / _SERIES_DIR / filename

    def exists(self, series_id: int, stamp: str) -> bool:
        return self.get_series_filepath(series_id, stamp).exists()

    def write_series_data(
        self, rows: list[dict], series_id: int, stamp: str
    ) -> Path:
        """Write a list of observation dicts to disk as JSON."""
        filepath = self.get_series_filepath(series_id, stamp)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Writing file %s", filepath)
        with filepath.open("wb") as f:
            f.write(orjson.dumps(rows, option=orjson.OPT_INDENT_2))
        return filepath

    @staticmethod
    def read_series_data(filepath: Path) -> list[dict]:
        """Read an observations JSON file from :meth:`write_series_data`."""
        logger.info("Reading file %s", filepath)
        with filepath.open("rb") as f:
            data = orjson.loads(f.read())
        if not isinstance(data, list):
            raise ValueError(f"Unexpected observations format in {filepath}")
        return data

    def read_series_dir(self) -> list[Path]:
        """Return the latest observation file per series in the data dir.

        Files are grouped by the base name before the ``@stamp`` suffix and
        only the file with the highest (lexicographic) stamp per series is
        returned.
        """
        dirpath = self.data_dir / _SERIES_DIR
        if not dirpath.exists():
            return []
        latest: dict[str, tuple[Path, str]] = {}
        for f in dirpath.glob("*.json"):
            stem = f.stem
            if "@" in stem:
                base, stamp = stem.rsplit("@", 1)
            else:
                base, stamp = stem, ""
            if base not in latest or stamp > latest[base][1]:
                latest[base] = (f, stamp)
        return [f for f, _ in latest.values()]

    # -- metadata --------------------------------------------------------

    def get_basic_metadata_filepath(self, series_id: int) -> Path:
        return (
            self.data_dir / _METADATA_DIR / f"{int(series_id):06d}_basic.json"
        )

    def get_full_metadata_filepath(self, series_id: int) -> Path:
        return (
            self.data_dir / _METADATA_DIR / f"{int(series_id):06d}_full.json"
        )

    def write_metadata(
        self, series_id: int, *, basic: dict | None, full: dict | None
    ) -> None:
        """Write basic and/or full metadata dicts for a series."""
        if basic is not None:
            path = self.get_basic_metadata_filepath(series_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                f.write(orjson.dumps(basic, option=orjson.OPT_INDENT_2))
        if full is not None:
            path = self.get_full_metadata_filepath(series_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                f.write(orjson.dumps(full, option=orjson.OPT_INDENT_2))

    def read_basic_metadata(self, series_id: int) -> dict | None:
        path = self.get_basic_metadata_filepath(series_id)
        if not path.exists():
            return None
        with path.open("rb") as f:
            return orjson.loads(f.read())

    def read_full_metadata(self, series_id: int) -> dict | None:
        path = self.get_full_metadata_filepath(series_id)
        if not path.exists():
            return None
        with path.open("rb") as f:
            return orjson.loads(f.read())

    def has_basic_metadata(self, series_id: int) -> bool:
        return self.get_basic_metadata_filepath(series_id).exists()

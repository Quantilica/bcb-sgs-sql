"""Load-from-files path (Via B): populate PostgreSQL from artifacts on disk.

This path never touches the BCB. It reads JSON observation files and
metadata JSON files and loads them through the same soft-versioned ETL used
by the fetch path. Two metadata layouts are understood (both written by
``bcb-sgs-fetcher``, see ``bcb_sgs_fetcher.storage``):

* combined ``{id:06d}.json`` (``{"basic": ..., "full": ...}``) — the bulk
  ``catalogo sync`` / ``metadata-bulk`` layout, optionally partitioned by
  month under ``bcb-sgs_{YYYY-MM}/metadata/``;
* split ``{id:06d}_basic.json`` / ``{id:06d}_full.json`` — the legacy
  single-series layout.

Also hosts the small dataclass/record ↔ row mapping helpers shared with
:mod:`~bcb_sgs_sql.sgs` and :mod:`~bcb_sgs_sql.toml_runner`.
"""

import datetime as dt
import logging
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path

import orjson
import sqlalchemy as sa

from . import database
from .config import Config
from .database import Row

logger = logging.getLogger(__name__)

# Portuguese frequency label (basic metadata) → SGS acronym. Only "D"
# triggers the daily-retroactive download strategy on the fetch path.
_FREQ_ACRONYM = {
    "diária": "D",
    "diario": "D",
    "diária ": "D",
    "semanal": "S",
    "mensal": "M",
    "trimestral": "T",
    "quadrimestral": "Qd",
    "anual": "A",
}


def _freq_acronym(frequency: str | None) -> str | None:
    if not frequency:
        return None
    return _FREQ_ACRONYM.get(frequency.strip().lower())


# ---------------------------------------------------------------------------
# Mapping helpers (shared with sgs.py)
# ---------------------------------------------------------------------------


def _parse_date(value) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _parse_value(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def point_to_record(point) -> dict:
    """Serialize a ``SeriesPoint`` to a JSON-friendly dict."""
    return {
        "series_id": point.series_id,
        "date": point.date.isoformat(),
        "value": str(point.value) if point.value is not None else None,
        "date_end": point.date_end.isoformat() if point.date_end else None,
    }


def record_to_tuple(record: dict) -> Row:
    """Convert an obs dict to ``(series_id, date, date_end, value)``."""
    return (
        int(record["series_id"]),
        _parse_date(record["date"]),
        _parse_date(record.get("date_end")),
        _parse_value(record.get("value")),
    )


def basic_to_metadata_row(
    basic: dict,
    full: dict | None = None,
    *,
    frequency_acronym: str | None = None,
    theme_id: int | None = None,
) -> dict:
    """Map a ``SeriesMetadataBasic`` dict (+ full) to a catalog row."""
    row: dict = {
        "series_id": int(basic["series_id"]),
        "name_index": basic.get("name"),
        "name": basic.get("name"),
        "name_abbreviated": basic.get("name_abbreviated"),
        "name_english": basic.get("name_english"),
        "name_english_abbreviated": basic.get("name_english_abbreviated"),
        "theme_hierarchy": basic.get("theme_hierarchy") or None,
        "frequency": basic.get("frequency"),
        "unit": basic.get("unit"),
        "source": basic.get("source"),
        "start_date": _parse_date(basic.get("start_date")),
        "last_date": _parse_date(basic.get("end_date")),
        "series_type": basic.get("series_type"),
        "precision": basic.get("precision"),
        "min_value": basic.get("min_value"),
        "max_value": basic.get("max_value"),
        "owner_manager": basic.get("manager_owner"),
        "special": basic.get("special"),
        "formula": basic.get("formula"),
        "series_primitive": basic.get("primitive_series"),
        "message_warning": basic.get("warning_message"),
    }
    if frequency_acronym is not None:
        row["frequency_acronym"] = frequency_acronym
    if theme_id is not None:
        row["theme_id"] = theme_id
    if full:
        row["last_update"] = _parse_date(full.get("last_update"))
        row["full_provider_data"] = full.get("provider_data")
        row["full_description"] = full.get("description")
        row["full_methodology"] = full.get("methodology")
        row["full_dissemination_formats"] = full.get("dissemination_formats")
    return row


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def read_json_rows(path: Path) -> list[Row]:
    """Read observation tuples from a JSON list-of-records file."""
    with path.open("rb") as f:
        data = orjson.loads(f.read())
    if not isinstance(data, list):
        raise ValueError(f"{path}: esperado uma lista de observações")
    return [record_to_tuple(r) for r in data]


def _is_combined_metadata(path: Path) -> bool:
    """True if ``path`` is a combined ``{"basic": ...}`` metadata file."""
    try:
        with path.open("rb") as f:
            data = orjson.loads(f.read())
    except (orjson.JSONDecodeError, ValueError, OSError):
        return False
    return isinstance(data, dict) and "basic" in data


def iter_metadata(path: Path) -> Iterator[tuple[dict, dict | None]]:
    """Yield ``(basic, full)`` dict pairs for every metadata file in a dir.

    Understands both the combined ``{id:06d}.json`` layout
    (``{"basic": ..., "full": ...}``) and the legacy split
    ``{id:06d}_basic.json`` (+ ``_full.json``) layout. HTML and observation
    files are ignored.
    """
    seen: set[Path] = set()
    # Combined files: any *.json that is not a split basic/full file.
    for combined in sorted(path.glob("*.json")):
        if combined.name.endswith(("_basic.json", "_full.json")):
            continue
        with combined.open("rb") as f:
            data = orjson.loads(f.read())
        if not isinstance(data, dict) or "basic" not in data:
            continue  # not a metadata file (e.g. an observation list)
        seen.add(combined)
        basic = data.get("basic")
        if basic:
            yield basic, data.get("full")
    # Legacy split files.
    for basic_path in sorted(path.glob("*_basic.json")):
        with basic_path.open("rb") as f:
            basic = orjson.loads(f.read())
        full_path = basic_path.with_name(
            basic_path.name.replace("_basic.json", "_full.json")
        )
        full = None
        if full_path.exists():
            with full_path.open("rb") as f:
                full = orjson.loads(f.read())
        yield basic, full


def _resolve_metadata_dir(root: Path) -> Path:
    """Resolve the directory that actually holds metadata JSON files.

    Accepts the metadata dir itself, a dataset root containing a
    ``metadata/`` subdir, or a dataset root containing month-partitioned
    ``bcb-sgs_{YYYY-MM}/metadata/`` dirs (newest wins).
    """
    if any(root.glob("*_basic.json")) or any(
        p
        for p in root.glob("*.json")
        if not p.name.endswith(("_basic.json", "_full.json"))
    ):
        return root
    direct = root / "metadata"
    if direct.exists():
        return direct
    partitioned = sorted(root.glob("bcb-sgs_*/metadata"), reverse=True)
    if partitioned:
        return partitioned[0]
    return root


def _resolve_data_dir(root: Path) -> Path | None:
    """Resolve the directory holding ``series_{id}@{ts}.json`` snapshots."""
    data = root / "data"
    if data.exists():
        return data
    if any(root.glob("series_*@*.json")):
        return root
    return None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_metadata_dir(config: Config, path: Path) -> int:
    """Load metadata files (combined or split) from a dir into PostgreSQL.

    ``path`` may be the metadata dir itself or a dataset root — the actual
    metadata dir is auto-resolved (incl. month-partitioned layouts). Theme
    hierarchies are upserted once per distinct hierarchy and memoized, so a
    full catalog (~19k series) costs a handful of theme transactions rather
    than one per series.
    """
    path = _resolve_metadata_dir(Path(path))
    engine = database.get_engine(config)
    database.create_all(engine, config.db_schema)
    rows: list[dict] = []
    theme_cache: dict[tuple[str, ...], int | None] = {}
    with engine.begin() as conn:
        for basic, full in iter_metadata(path):
            hierarchy = basic.get("theme_hierarchy") or None
            key = tuple(hierarchy) if hierarchy else ()
            if key not in theme_cache:
                theme_cache[key] = database.upsert_theme_hierarchy(conn, hierarchy)
            rows.append(
                basic_to_metadata_row(
                    basic,
                    full,
                    frequency_acronym=_freq_acronym(basic.get("frequency")),
                    theme_id=theme_cache[key],
                )
            )
    n = database.save_series_metadata(engine, rows)
    logger.info("Loaded metadata for %d series from %s", n, path)
    removed = database.prune_empty_themes(engine)
    if removed:
        logger.info("Pruned %d theme(s) with no series in their subtree", removed)
    return n


def load_observation_files(
    engine: sa.engine.Engine,
    files: list[Path],
    *,
    force_load: bool = False,
) -> tuple[int, int, int]:
    """Load JSON observation files with file-level idempotency.

    Skips files already in ``arquivo_carregado``, loads the rest through the
    soft-versioned ETL, then records the loaded files. Shared by the ``run``
    and ``load`` paths so both skip and record the same way.
    """
    names = {f.name for f in files}
    skip = set() if force_load else database.get_loaded_filenames(engine, names)

    all_rows: list[Row] = []
    loaded_names: list[str] = []
    for f in files:
        if f.name in skip:
            logger.info("Skipping already-loaded file %s", f.name)
            continue
        if f.suffix == ".json":
            all_rows.extend(read_json_rows(f))
        else:
            logger.warning("Ignoring unsupported file %s", f)
            continue
        loaded_names.append(f.name)

    result = database.load_series_data(engine, all_rows)
    database.record_loaded_files(engine, loaded_names)
    return result


def load_observations(
    config: Config,
    files: list[Path],
    force_load: bool = False,
) -> tuple[int, int, int]:
    """Load JSON observation files with file-level idempotency."""
    engine = database.get_engine(config)
    database.create_all(engine, config.db_schema)
    return load_observation_files(engine, files, force_load=force_load)


def _classify(path: Path) -> str:
    """Sniff whether a directory (or dataset root) holds metadata or json.

    Resolves the metadata dir first (handles the ``metadata/`` subdir and
    month-partitioned layouts), then checks for split or combined metadata
    files; otherwise treats the path as observation files.
    """
    meta_dir = _resolve_metadata_dir(path)
    if any(meta_dir.glob("*_basic.json")):
        return "metadata"
    for combined in meta_dir.glob("*.json"):
        if combined.name.endswith(("_basic.json", "_full.json")):
            continue
        if _is_combined_metadata(combined):
            return "metadata"
        break
    return "json"


def _gather_observation_files(path: Path) -> list[Path]:
    """Collect observation JSON files under ``path`` (excludes metadata)."""
    return [
        f
        for f in sorted(path.rglob("*.json"))
        if not f.name.endswith(("_basic.json", "_full.json"))
        and "metadata" not in f.parts
    ]


def load(
    config: Config,
    path: Path | None = None,
    kind: str = "auto",
    force_load: bool = False,
    with_data: bool = False,
) -> None:
    """Entry point for the ``load`` CLI command.

    ``path`` defaults to ``config.data_dir`` (the dataset root). ``kind``:
    ``json``, ``metadata`` or ``auto`` (sniff). When ``with_data`` is set on
    a metadata load, observations under ``<root>/data/`` are loaded right
    after the metadata (metadata first, to satisfy the ``series_data`` →
    ``series_metadata`` foreign key).
    """
    root = Path(path) if path is not None else config.data_dir
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    if root.is_file():
        if (
            kind == "metadata"
            or root.name.endswith("_basic.json")
            or _is_combined_metadata(root)
        ):
            load_metadata_dir(config, root.parent)
            return
        load_observations(config, [root], force_load=force_load)
        return

    if kind == "auto":
        kind = _classify(root)

    if kind == "metadata":
        load_metadata_dir(config, root)
        if with_data:
            data_dir = _resolve_data_dir(root)
            if data_dir is None:
                logger.warning("--with-data: no data/ dir found under %s", root)
            else:
                load_observations(
                    config,
                    _gather_observation_files(data_dir),
                    force_load=force_load,
                )
    elif kind == "json":
        data_dir = _resolve_data_dir(root) or root
        load_observations(
            config, _gather_observation_files(data_dir), force_load=force_load
        )
    else:
        raise ValueError(f"Unknown load kind: {kind!r}")

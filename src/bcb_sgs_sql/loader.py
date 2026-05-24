"""Load-from-files path (Via B): populate PostgreSQL from artifacts on disk.

This path never touches the BCB. It reads JSON observation files and
metadata JSON files (the ``{id:06d}_{basic,full}.json`` layout written by
``bcb-sgs-fetcher``) and loads them through the same soft-versioned ETL
used by the fetch path.

Also hosts the small dataclass/record ↔ row mapping helpers shared with
:mod:`~bcb_sgs_sql.sgs`.
"""

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

import orjson

from . import database
from .config import Config
from .database import Row

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_metadata_dir(config: Config, path: Path) -> int:
    """Load {id}_basic.json / {id}_full.json metadata files from a dir."""
    engine = database.get_engine(config)
    rows: list[dict] = []
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
        with engine.begin() as conn:
            theme_id = database.upsert_theme_hierarchy(
                conn, basic.get("theme_hierarchy")
            )
        rows.append(basic_to_metadata_row(basic, full, theme_id=theme_id))
    n = database.save_series_metadata(engine, rows)
    logger.info("Loaded metadata for %d series from %s", n, path)
    return n


def load_observations(
    config: Config,
    files: list[Path],
    force_load: bool = False,
) -> tuple[int, int, int]:
    """Load JSON observation files with file-level idempotency."""
    engine = database.get_engine(config)
    names = {f.name for f in files}
    skip = (
        set()
        if force_load
        else database.get_loaded_filenames(engine, names)
    )

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


def _classify(path: Path) -> str:
    """Sniff whether a directory holds metadata or json observation files."""
    if list(path.glob("*_basic.json")):
        return "metadata"
    return "json"


def load(
    config: Config,
    path: Path,
    kind: str = "auto",
    force_load: bool = False,
) -> None:
    """Entry point for the ``load`` CLI command.

    ``kind``: ``json``, ``metadata`` or ``auto`` (sniff).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    if path.is_file():
        if kind == "metadata" or path.name.endswith("_basic.json"):
            load_metadata_dir(config, path.parent)
        else:
            load_observations(config, [path], force_load=force_load)
        return

    if kind == "auto":
        kind = _classify(path)

    if kind == "metadata":
        load_metadata_dir(config, path)
    elif kind == "json":
        files = [
            f
            for f in sorted(path.rglob("*.json"))
            if not f.name.endswith(("_basic.json", "_full.json"))
        ]
        load_observations(config, files, force_load=force_load)
    else:
        raise ValueError(f"Unknown load kind: {kind!r}")

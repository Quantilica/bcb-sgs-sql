"""Database helpers: engine creation and data-loading functions.

Public functions:

- :func:`get_engine` — create a SQLAlchemy engine from :class:`Config`.
- :func:`upsert_theme_hierarchy` — idempotently insert a theme chain and
  return the leaf ``theme.id``.
- :func:`prune_empty_themes` — delete themes with no series in their subtree.
- :func:`save_series_metadata` — upsert series catalog rows.
- :func:`load_series_data` — soft-versioned load of observations.
- :func:`get_loaded_filenames` / :func:`record_loaded_files` — file-level
  idempotency tracking.

Soft-versioning (see :mod:`bcb_sgs_sql.models`): a load never overwrites a
value. For each incoming ``(series_id, date, date_end)`` whose value
*differs* from the current active row, the prior active row is flipped
``ativo = FALSE`` and a new row is inserted with the current ``loaded_at``.
Incoming observations equal to the current active value are skipped, so
re-running an identical load causes zero churn.
"""

import datetime as dt
import itertools
import logging
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import models
from .config import Config

logger = logging.getLogger(__name__)

_BATCH_SIZE = 5000

Row = tuple[int, dt.date, dt.date | None, object]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def get_engine(config: Config) -> sa.engine.Engine:
    """Create and return a SQLAlchemy engine for the configured DB."""
    connection_string = (
        f"postgresql+psycopg://{config.db_user}:{config.db_password}"
        f"@{config.db_host}:{config.db_port}/{config.db_name}"
    )
    return sa.create_engine(
        connection_string,
        connect_args={"options": f"-c search_path={config.db_schema}"},
    )


def create_all(engine: sa.engine.Engine, schema: str) -> None:
    """Create the schema (if missing) and all tables."""
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    models.Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------


def upsert_theme_hierarchy(
    conn: sa.Connection, theme_hierarchy: Iterable[str] | None
) -> int | None:
    """Insert a theme chain level by level; return the leaf ``theme.id``.

    Idempotent via the ``uq_theme`` constraint. Sibling chains share their
    common ancestors. Returns ``None`` for an empty/missing hierarchy.
    """
    if not theme_hierarchy:
        return None

    parent_id: int | None = None
    for level, name in enumerate(theme_hierarchy, start=1):
        if name is None:
            continue
        stmt = pg_insert(models.Theme.__table__).values(
            name=name, level=level, parent_id=parent_id
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_theme",
            set_={"name": stmt.excluded.name},
        ).returning(models.Theme.id)
        parent_id = conn.execute(stmt).scalar_one()
    return parent_id


def prune_empty_themes(engine: sa.engine.Engine) -> int:
    """Delete themes that have no series anywhere in their subtree.

    A theme is kept when it (or any of its descendants) is referenced by at
    least one series; every other theme is removed. The whole tree is loaded
    once and pruned in memory, so this runs in a couple of queries regardless
    of hierarchy depth. Returns the number of themes deleted.
    """
    theme = models.Theme.__table__
    series = models.SeriesMetadata.__table__
    with engine.begin() as conn:
        themes = conn.execute(
            sa.select(theme.c.id, theme.c.parent_id, theme.c.level)
        ).all()
        if not themes:
            return 0

        referenced = {
            row.theme_id
            for row in conn.execute(
                sa.select(series.c.theme_id)
                .where(series.c.theme_id.isnot(None))
                .distinct()
            )
        }

        parent_of = {row.id: row.parent_id for row in themes}

        # Keep every referenced theme plus all of its ancestors.
        keep: set[int] = set()
        for theme_id in referenced:
            current = theme_id
            while current is not None and current not in keep:
                keep.add(current)
                current = parent_of.get(current)

        # Group the doomed themes by level and delete the deepest first, so the
        # self-referential parent_id foreign key is never violated.
        by_level: dict[int, list[int]] = {}
        for row in themes:
            if row.id not in keep:
                by_level.setdefault(row.level, []).append(row.id)

        deleted = 0
        for level in sorted(by_level, reverse=True):
            ids = by_level[level]
            conn.execute(sa.delete(theme).where(theme.c.id.in_(ids)))
            deleted += len(ids)

    return deleted


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

_METADATA_UPDATE_COLS = (
    "name_index",
    "name",
    "name_abbreviated",
    "name_english",
    "name_english_abbreviated",
    "theme_hierarchy",
    "frequency_acronym",
    "frequency",
    "unit",
    "source",
    "start_date",
    "last_date",
    "last_date_index",
    "series_type",
    "precision",
    "max_value",
    "min_value",
    "active",
    "special",
    "formula",
    "series_primitive",
    "owner_manager",
    "message",
    "message_warning",
    "full_provider_data",
    "full_description",
    "full_methodology",
    "full_dissemination_formats",
    "last_update",
    "theme_id",
)


def save_series_metadata(engine: sa.engine.Engine, rows: list[dict]) -> int:
    """Upsert series catalog rows (keyed on ``series_id``).

    Each dict must contain ``series_id`` and any subset of the metadata
    columns. Missing columns are left untouched on update / NULL on insert.
    Returns the number of rows upserted.
    """
    if not rows:
        return 0
    # Cap the batch so rows × columns stays under Postgres' 65535-parameter
    # limit (full-catalog loads upsert ~19k rows of ~30 columns each).
    max_cols = len(_METADATA_UPDATE_COLS) + 1  # + series_id
    batch_size = min(_BATCH_SIZE, max(1, 65535 // max_cols))
    total = 0
    with engine.begin() as conn:
        rows_iter = iter(rows)
        while True:
            batch = list(itertools.islice(rows_iter, batch_size))
            if not batch:
                break
            # Rows may carry heterogeneous key sets (optional columns are
            # omitted when absent); normalize each batch to a uniform shape
            # so the multi-row VALUES clause is well-formed.
            keys = {k for r in batch for k in r}
            norm = [{k: r.get(k) for k in keys} for r in batch]
            present = keys & set(_METADATA_UPDATE_COLS)
            stmt = pg_insert(models.SeriesMetadata.__table__).values(norm)
            stmt = stmt.on_conflict_do_update(
                index_elements=["series_id"],
                set_={c: getattr(stmt.excluded, c) for c in present},
            )
            conn.execute(stmt)
            total += len(batch)
    return total


# ---------------------------------------------------------------------------
# Observations (soft-versioned ETL)
# ---------------------------------------------------------------------------

_STAGING_DDL = (
    "CREATE TEMP TABLE _staging_series_data ("
    "  series_id integer,"
    "  date date,"
    "  date_end date,"
    "  value numeric"
    ") ON COMMIT DROP"
)

_STAGING_COPY = (
    "COPY _staging_series_data (series_id, date, date_end, value) FROM STDIN"
)

# Create stub catalog rows so the series_data FK is satisfied even for a
# values-only load (metadata can be upserted later).
_ENSURE_SERIES = (
    "INSERT INTO series_metadata (series_id)"
    " SELECT DISTINCT series_id FROM _staging_series_data"
    " ON CONFLICT (series_id) DO NOTHING"
)

# Deactivate prior active rows whose value differs from the incoming one.
_DEACTIVATE = (
    "UPDATE series_data d"
    " SET ativo = FALSE"
    " FROM _staging_series_data s"
    " WHERE d.series_id = s.series_id"
    "  AND d.date = s.date"
    "  AND d.date_end IS NOT DISTINCT FROM s.date_end"
    "  AND d.ativo = TRUE"
    "  AND d.value IS DISTINCT FROM s.value"
)

# Insert incoming rows that are new keys or changed values. Runs AFTER the
# deactivate, so changed keys no longer have an active row to collide with.
_INSERT = (
    "INSERT INTO series_data"
    " (series_id, date, date_end, value, loaded_at, ativo)"
    " SELECT s.series_id, s.date, s.date_end, s.value, %(loaded_at)s, TRUE"
    " FROM _staging_series_data s"
    " LEFT JOIN series_data d"
    "  ON d.series_id = s.series_id"
    "  AND d.date = s.date"
    "  AND d.date_end IS NOT DISTINCT FROM s.date_end"
    "  AND d.ativo = TRUE"
    " WHERE d.id IS NULL OR d.value IS DISTINCT FROM s.value"
)


def _dedup(rows: Iterable[Row]) -> list[Row]:
    """Collapse duplicate keys within a batch (last occurrence wins)."""
    seen: dict[tuple, Row] = {}
    for r in rows:
        seen[(r[0], r[1], r[2])] = r
    return list(seen.values())


def load_series_data(
    engine: sa.engine.Engine,
    rows: Iterable[Row],
    loaded_at: dt.datetime | None = None,
) -> tuple[int, int, int]:
    """Soft-versioned load of observations.

    ``rows`` yields ``(series_id, date, date_end, value)`` tuples.
    Returns ``(n_staging, n_inserted, n_deactivated)``.
    """
    loaded_at = loaded_at or dt.datetime.now(dt.UTC)
    deduped = _dedup(rows)
    if not deduped:
        return (0, 0, 0)

    with engine.connect() as conn:
        raw_conn = conn.connection.dbapi_connection
        with raw_conn.cursor() as cur:
            cur.execute(_STAGING_DDL)
            with cur.copy(_STAGING_COPY) as copy:
                for series_id, date, date_end, value in deduped:
                    copy.write_row((series_id, date, date_end, value))
            cur.execute(_ENSURE_SERIES)
            cur.execute(_DEACTIVATE)
            n_deactivated = cur.rowcount
            cur.execute(_INSERT, {"loaded_at": loaded_at})
            n_inserted = cur.rowcount
        raw_conn.commit()

    logger.info(
        "Loaded %d/%d observations (%d deactivated)",
        n_inserted,
        len(deduped),
        n_deactivated,
    )
    return (len(deduped), n_inserted, n_deactivated)


# ---------------------------------------------------------------------------
# File-level idempotency
# ---------------------------------------------------------------------------


def get_loaded_filenames(engine: sa.engine.Engine, filenames: set[str]) -> set[str]:
    """Return the subset of filenames already recorded in arquivo_carregado."""
    if not filenames:
        return set()
    with engine.connect() as conn:
        result = conn.execute(
            sa.select(models.ArquivoCarregado.arquivo).where(
                models.ArquivoCarregado.arquivo.in_(filenames)
            )
        )
        return {row.arquivo for row in result}


def record_loaded_files(
    engine: sa.engine.Engine,
    filenames: Iterable[str],
    series_id: int | None = None,
) -> None:
    """Record loaded source files (idempotent)."""
    values = [{"arquivo": name, "series_id": series_id} for name in filenames]
    if not values:
        return
    with engine.begin() as conn:
        stmt = pg_insert(models.ArquivoCarregado.__table__).values(values)
        conn.execute(stmt.on_conflict_do_nothing())

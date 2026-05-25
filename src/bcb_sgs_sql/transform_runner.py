"""Pipeline runner for SQL transformations defined by ``transform.toml``.

A pipeline directory contains one ``transform.toml`` declaring one or more
output tables/views. Each ``[[table]]`` entry references its own ``.sql``
file in the same directory.

TOML schema
-----------
::

    [[table]]
    name        = "precos"
    schema      = "analytics"
    strategy    = "replace"     # "replace" or "view"
    sql         = "precos.sql"
    description = "Painel wide de índices de preços"

    [[table]]
    name        = "selic"
    schema      = "analytics"
    strategy    = "view"
    sql         = "selic.sql"

Required fields per entry: ``name``, ``schema``, ``strategy``, ``sql``.
Optional: ``description``, ``primary_key``, ``indexes``.

Strategies
~~~~~~~~~~
``replace``
    ``DROP TABLE IF EXISTS`` followed by ``CREATE TABLE ... AS``.
    Full refresh — best for batch imports into Power BI, Excel, etc.

``view``
    ``CREATE OR REPLACE VIEW``.  Zero storage cost, always up-to-date,
    best for live database connections.

Each entry runs in its own transaction; if one fails, previously
materialized outputs from the same pipeline persist.

The SQL queries use unqualified table names (``series_data``,
``series_metadata``, ``theme``).  They resolve via the database
``search_path`` set by ``get_engine`` from ``config.ini``.
"""

import logging
import tomllib
from pathlib import Path

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from . import database
from .config import Config

logger = logging.getLogger(__name__)


class TransformRunner:
    """Run SQL transformations declared in a ``transform.toml`` file."""

    def __init__(self, config: Config, toml_path: Path, console: Console | None = None):
        self.config = config
        self.toml_path = toml_path
        self.console = console

    def run(self):
        with open(self.toml_path, "rb") as f:
            data = tomllib.load(f)

        tables = data.get("table")
        if not isinstance(tables, list) or not tables:
            raise ValueError(
                f"{self.toml_path}: esperado um ou mais [[table]] (array)."
            )

        engine = database.get_engine(self.config)

        with Progress(
            SpinnerColumn(finished_text="[green]✓[/green]"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
            disable=self.console is None,
        ) as progress:
            for entry in tables:
                self._materialize(engine, entry, progress)

    def _materialize(self, engine, entry: dict, progress: Progress) -> None:
        missing = [f for f in ("name", "schema", "strategy", "sql") if f not in entry]
        if missing:
            raise ValueError(
                f"{self.toml_path}: [[table]] sem campo(s) obrigatório(s): "
                f"{', '.join(missing)}"
            )

        name = entry["name"]
        schema = entry["schema"]
        strategy = entry["strategy"]
        sql_rel = entry["sql"]
        primary_key = entry.get("primary_key")
        indexes = entry.get("indexes", [])

        sql_path = self.toml_path.parent / sql_rel
        if not sql_path.exists():
            raise FileNotFoundError(
                f"{self.toml_path}: arquivo SQL '{sql_rel}' não encontrado "
                f"em {self.toml_path.parent}"
            )
        query = sql_path.read_text(encoding="utf-8").strip().replace("%", "%%")

        qualified = f'"{schema}"."{name}"'
        strategy_label = {"replace": "tabela", "view": "view"}.get(strategy, strategy)
        task = progress.add_task(
            f"{qualified} [dim][{strategy_label}][/dim]", total=None
        )

        with engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

            if strategy == "view":
                conn.exec_driver_sql(f"CREATE OR REPLACE VIEW {qualified} AS\n{query}")
            elif strategy == "replace":
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {qualified}")
                conn.exec_driver_sql(f"CREATE TABLE {qualified} AS\n{query}")

                if primary_key:
                    pk_cols = ", ".join(f'"{c}"' for c in primary_key)
                    conn.exec_driver_sql(
                        f"ALTER TABLE {qualified} ADD PRIMARY KEY ({pk_cols})"
                    )

                for idx in indexes:
                    idx_name = idx["name"]
                    idx_cols = ", ".join(f'"{c}"' for c in idx["columns"])
                    unique = "UNIQUE" if idx.get("unique") else ""
                    conn.exec_driver_sql(
                        f'CREATE {unique} INDEX "{idx_name}" ON '
                        f"{qualified} ({idx_cols})"
                    )
            else:
                raise ValueError(
                    f"Unknown strategy {strategy!r} for {qualified} in {self.toml_path}"
                )

        progress.update(task, total=1, completed=1)

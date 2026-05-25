"""Pipeline runner driven by a ``fetch.toml`` configuration file.

``TomlScript`` reads a TOML file that declares which BCB SGS series to
fetch and drives the full ETL pipeline: metadata → download → load.

TOML schema
-----------
Each ``[[series]]`` entry is a selector. Either ``ids`` (always valid) or
``themes`` (requires a populated catalog) must be present; ``frequency``
(acronym ``D``/``S``/``M``/``T``/``Qd``/``A``) optionally narrows a
``themes`` selector.

::

    [[series]]
    ids = [433, 13522, 189]

    [[series]]
    themes = ["Índices de preços"]
    frequency = "M"

    [[series]]
    ids = [11, 12]
"""

import dataclasses
import datetime as dt
import logging
import tomllib
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from bcb_sgs_fetcher import storage
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from . import database, sgs
from .config import Config
from .loader import basic_to_metadata_row, load_observation_files

logger = logging.getLogger(__name__)

# Portuguese frequency label (basic metadata) → SGS acronym. Only "D"
# triggers the daily-retroactive download strategy.
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


class _MainOnlyTimeElapsedColumn(TimeElapsedColumn):
    def render(self, task):
        if not task.fields.get("main"):
            return Text("")
        return super().render(task)


class _MainOnlyTimeRemainingColumn(TimeRemainingColumn):
    def render(self, task):
        if not task.fields.get("main"):
            return Text("")
        return super().render(task)


def _make_progress(console: Console | None) -> Progress:
    return Progress(
        SpinnerColumn(finished_text="[green]✓[/green]"),
        TextColumn("[progress.description]{task.description}", table_column=None),
        BarColumn(bar_width=28),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%", style="grey70"),
        _MainOnlyTimeElapsedColumn(),
        _MainOnlyTimeRemainingColumn(),
        console=console,
        transient=False,
        disable=console is None,
    )


def _make_download_progress(console: Console | None) -> Progress:
    return Progress(
        SpinnerColumn(finished_text="[green]✓[/green]"),
        TextColumn("[progress.description]{task.description}", table_column=None),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        _MainOnlyTimeElapsedColumn(),
        _MainOnlyTimeRemainingColumn(),
        console=console,
        transient=False,
        disable=console is None,
    )


class TomlScript:
    """ETL pipeline runner that loads series definitions from a TOML file."""

    def __init__(
        self,
        config: Config,
        toml_path: Path,
        max_workers: int = 4,
        force_metadata: bool = False,
        force_load: bool = False,
        console: Console | None = None,
        cache_ttl_hours: float | None = None,
    ):
        self.config = config
        self.toml_path = toml_path
        self.max_workers = max_workers
        self.force_metadata = force_metadata
        self.force_load = force_load
        self.console = console
        self.cache_ttl_hours = (
            cache_ttl_hours if cache_ttl_hours is not None else config.cache_ttl_hours
        )
        self.data_dir = config.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_selectors(self) -> list[dict]:
        with open(self.toml_path, "rb") as f:
            data = tomllib.load(f)
        selectors = data.get("series")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(
                f"{self.toml_path}: esperado um ou mais [[series]] (array)."
            )
        return selectors

    def load_metadata(
        self,
        engine: sa.engine.Engine,
        fetcher: sgs.Fetcher,
        series_ids: list[int],
        *,
        on_done: Callable[[], None] | None = None,
    ) -> dict[int, str | None]:
        """Fetch + persist metadata; return a series_id → freq-acronym map."""
        rows: list[dict] = []
        freq_by_id: dict[int, str | None] = {}
        for series_id in series_ids:
            basic, full = fetcher.fetch_metadata(series_id, force=self.force_metadata)
            with engine.begin() as conn:
                theme_id = database.upsert_theme_hierarchy(conn, basic.theme_hierarchy)
            acronym = _freq_acronym(basic.frequency)
            freq_by_id[series_id] = acronym
            rows.append(
                basic_to_metadata_row(
                    dataclasses.asdict(basic),
                    dataclasses.asdict(full),
                    frequency_acronym=acronym,
                    theme_id=theme_id,
                )
            )
            if on_done is not None:
                on_done()
        database.save_series_metadata(engine, rows)
        return freq_by_id

    def run(self):
        engine = database.get_engine(self.config)
        database.create_all(engine, self.config.db_schema)
        try:
            self._run(engine)
        except KeyboardInterrupt:
            if self.console is not None:
                self.console.print("\n[yellow]Interrompido.[/yellow]")
            raise SystemExit(1) from None

    def _run(self, engine: sa.engine.Engine):
        selectors = self.get_selectors()
        with sgs.Fetcher(self.data_dir, max_workers=self.max_workers) as fetcher:
            series_ids = fetcher.plan_series(selectors, engine=engine)

            if self.console is not None:
                info = Table.grid(padding=(0, 2))
                info.add_column(style="bold")
                info.add_column()
                info.add_row("Pipeline", str(self.toml_path))
                info.add_row("Séries", str(len(series_ids)))
                info.add_row("Threads", str(self.max_workers))
                info.add_row(
                    "Banco",
                    f"{self.config.db_host}:{self.config.db_port}"
                    f"/{self.config.db_name}  schema={self.config.db_schema}",
                )
                info.add_row("Storage", str(self.config.data_dir))
                self.console.print(info)
                self.console.print()

            with _make_progress(self.console) as progress:
                n = len(series_ids)
                meta_task = progress.add_task(
                    f"Metadados ({n} série{'s' if n != 1 else ''})",
                    total=n,
                    main=True,
                )
                freq_by_id = self.load_metadata(
                    engine,
                    fetcher,
                    series_ids,
                    on_done=lambda: progress.advance(meta_task),
                )
                progress.update(meta_task, description=f"Metadados ({n}) ✓")

            now = dt.datetime.now()
            ttl = dt.timedelta(hours=self.cache_ttl_hours)
            to_download = []
            for sid in series_ids:
                if self.force_load:
                    to_download.append(sid)
                    continue
                ts = storage.latest_series_datetime(self.data_dir, sid)
                if ts is None or (now - ts) > ttl:
                    to_download.append(sid)

            with _make_download_progress(self.console) as progress:
                dl_task = progress.add_task(
                    "Download", total=len(to_download), main=True
                )
                fetcher.download_series(
                    to_download,
                    frequency_by_id=freq_by_id,
                    on_done=lambda: progress.advance(dl_task),
                )
                progress.update(dl_task, description="Download concluído ✓")

        files = []
        for sid in series_ids:
            f = storage.latest_series_file(self.data_dir, sid)
            if f is not None:
                files.append(f)

        with _make_progress(self.console) as progress:
            db_task = progress.add_task(
                "Carregando no banco de dados", total=None, main=True
            )
            n_staging, n_inserted, n_deactivated = load_observation_files(
                engine, files, force_load=self.force_load
            )
            progress.update(
                db_task,
                total=1,
                completed=1,
                description="Carregamento concluído ✓",
            )
        n_cache = len(series_ids) - len(to_download)
        if self.console is not None:
            self.console.print(
                f"  [green]✓[/green] {n_inserted} observações inseridas, "
                f"{n_deactivated} desativadas "
                f"({len(series_ids)} séries: {n_cache} do cache, "
                f"{len(to_download)} baixadas)"
            )

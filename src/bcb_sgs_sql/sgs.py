"""Fetch-and-load path (Via A): a thin wrapper around ``bcb-sgs-fetcher``.

:class:`Fetcher` owns an :class:`SgsDataClient` (values JSON API) and,
lazily, a :class:`ScraperClient` (metadata/theme HTML scraping). It plans
which series to fetch from ``fetch.toml`` selectors, downloads observations
concurrently with retry/backoff, and caches results to disk via
:class:`~bcb_sgs_sql.storage.Storage` so re-runs hit the cache.

The daily-retroactive walk for high-frequency series is *not*
reimplemented here — it lives in ``bcb_sgs_fetcher.get_daily_series`` and is
triggered by passing ``frequency_acronym="D"``.
"""

import dataclasses
import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import TracebackType

import sqlalchemy as sa
from bcb_sgs_fetcher import (
    ScraperClient,
    SeriesMetadataBasic,
    SeriesMetadataFull,
    SgsDataClient,
    parse_metadata_basic,
    parse_metadata_full,
)
from bcb_sgs_fetcher.constants import BASIC, FULL

from . import models
from .storage import Storage

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_BACKOFF_BASE = 5  # seconds: 5, 10, 20, 40, 80


class Fetcher:
    """Wrap the BCB SGS clients with caching and retry."""

    def __init__(
        self,
        storage: Storage,
        max_workers: int = 4,
        sleep: float = 0.0,
    ) -> None:
        self.storage = storage
        self.max_workers = max_workers
        self.sleep = sleep
        self._data_client: SgsDataClient | None = None
        self._scraper: ScraperClient | None = None

    def __enter__(self) -> "Fetcher":
        self._data_client = SgsDataClient()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._data_client is not None:
            self._data_client.close()
            self._data_client = None
        if self._scraper is not None:
            self._scraper.close()
            self._scraper = None

    def _ensure_scraper(self) -> ScraperClient:
        if self._scraper is None:
            self._scraper = ScraperClient()
        return self._scraper

    # -- planning --------------------------------------------------------

    def plan_series(
        self,
        selectors: Iterable[dict],
        engine: sa.engine.Engine | None = None,
    ) -> list[int]:
        """Resolve ``fetch.toml`` ``[[series]]`` selectors into series ids.

        ``ids`` selectors are always valid. ``themes`` / ``frequency``
        selectors query the catalog (require ``engine`` and a populated
        ``series_metadata``).
        """
        ids: set[int] = set()
        for sel in selectors:
            if sel.get("ids"):
                ids.update(int(x) for x in sel["ids"])
            elif sel.get("themes"):
                if engine is None:
                    raise ValueError(
                        "selector 'themes' requires a populated catalog; "
                        "run metadata first"
                    )
                ids.update(
                    self._query_by_theme(
                        engine, sel["themes"], sel.get("frequency")
                    )
                )
            else:
                raise ValueError(
                    "each [[series]] entry needs 'ids' or 'themes'"
                )
        return sorted(ids)

    @staticmethod
    def _query_by_theme(
        engine: sa.engine.Engine,
        themes: list[str],
        frequency: str | None,
    ) -> list[int]:
        stmt = sa.select(models.SeriesMetadata.series_id).where(
            models.SeriesMetadata.theme_hierarchy.overlap(themes)
        )
        if frequency:
            stmt = stmt.where(
                models.SeriesMetadata.frequency_acronym == frequency
            )
        with engine.connect() as conn:
            return [row.series_id for row in conn.execute(stmt)]

    # -- metadata --------------------------------------------------------

    def fetch_metadata(
        self, series_id: int, *, force: bool = False
    ) -> tuple[SeriesMetadataBasic, SeriesMetadataFull]:
        """Fetch (and cache) basic + full metadata for a series."""
        if not force and self.storage.has_basic_metadata(series_id):
            basic_d = self.storage.read_basic_metadata(series_id)
            full_d = self.storage.read_full_metadata(series_id)
            basic = SeriesMetadataBasic(**basic_d)
            full = (
                SeriesMetadataFull(**full_d)
                if full_d is not None
                else SeriesMetadataFull()
            )
            return basic, full

        scraper = self._ensure_scraper()
        html = scraper.request_metadata_html(series_id)
        basic = parse_metadata_basic(html[BASIC])
        full = parse_metadata_full(html[FULL])
        self.storage.write_metadata(
            series_id,
            basic=dataclasses.asdict(basic),
            full=dataclasses.asdict(full),
        )
        return basic, full

    # -- observations ----------------------------------------------------

    def _fetch_one(
        self, series_id: int, frequency_acronym: str | None
    ) -> list:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._data_client.fetch_series_data(
                    series_id, frequency_acronym=frequency_acronym
                )
            except Exception as e:  # noqa: BLE001 — transient network errors
                last_exc = e
                wait = _BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "Error fetching series %s (attempt %d/%d): %s; "
                    "retrying in %ds",
                    series_id,
                    attempt + 1,
                    _MAX_RETRIES,
                    e,
                    wait,
                )
                time.sleep(wait)
        raise RuntimeError(
            f"Failed to fetch series {series_id} after "
            f"{_MAX_RETRIES} attempts"
        ) from last_exc

    def download_series(
        self,
        series_ids: list[int],
        frequency_by_id: dict[int, str] | None = None,
        on_done=None,
    ) -> list:
        """Download observations for many series concurrently.

        Returns a flat list of ``SeriesPoint``. Each series is also cached
        to disk as a JSON observations file.
        """
        frequency_by_id = frequency_by_id or {}
        all_points: list = []

        def task(series_id: int) -> list:
            freq = frequency_by_id.get(series_id)
            points = self._fetch_one(series_id, freq)
            if points:
                from .loader import point_to_record

                self.storage.write_series_data(
                    [point_to_record(p) for p in points],
                    series_id,
                    stamp=time.strftime("%Y%m%dT%H%M%S"),
                )
            if self.sleep:
                time.sleep(self.sleep)
            return points

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(task, sid): sid for sid in series_ids
            }
            for fut in as_completed(futures):
                all_points.extend(fut.result())
                if on_done is not None:
                    on_done()
        return all_points

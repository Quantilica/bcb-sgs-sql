"""Fetch-and-load path (Via A): a thin wrapper around ``bcb-sgs-fetcher``.

:class:`Fetcher` owns an :class:`SgsDataClient` (values JSON API) and,
lazily, a :class:`ScraperClient` (metadata/theme HTML scraping). It plans
which series to fetch from ``fetch.toml`` selectors, downloads observations
concurrently with retry/backoff, and caches results to disk via
``bcb_sgs_fetcher.storage`` so re-runs hit the cache.

The daily-retroactive walk for high-frequency series is *not*
reimplemented here — it lives in ``bcb_sgs_fetcher.get_daily_series`` and is
triggered by passing ``frequency_acronym="D"``.
"""

import dataclasses
import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import TracebackType

import sqlalchemy as sa
from bcb_sgs_fetcher import (
    ScraperClient,
    SeriesMetadataBasic,
    SeriesMetadataFull,
    SgsDataClient,
    parse_metadata_basic,
    parse_metadata_full,
    storage,
)
from bcb_sgs_fetcher.constants import BASIC, FULL

from . import models

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_BACKOFF_BASE = 5  # seconds: 5, 10, 20, 40, 80


def _records_equal(old: list[dict], new: list[dict]) -> bool:
    """Compare two observation-record lists, insensitive to ordering."""

    def key(r: dict) -> tuple[str, str]:
        return (r.get("date") or "", r.get("date_end") or "")

    return sorted(old, key=key) == sorted(new, key=key)


class Fetcher:
    """Wrap the BCB SGS clients with caching and retry."""

    def __init__(
        self,
        data_dir: Path,
        max_workers: int = 4,
        sleep: float = 0.0,
    ) -> None:
        self.data_dir = data_dir
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

    @staticmethod
    def _build_metadata(
        basic_d: dict, full_d: dict | None
    ) -> tuple[SeriesMetadataBasic, SeriesMetadataFull]:
        """Rebuild the metadata dataclasses from cached dicts."""
        basic = SeriesMetadataBasic(**basic_d)
        full = (
            SeriesMetadataFull(**full_d)
            if full_d is not None
            else SeriesMetadataFull()
        )
        return basic, full

    def fetch_metadata(
        self, series_id: int, *, force: bool = False
    ) -> tuple[SeriesMetadataBasic, SeriesMetadataFull]:
        """Fetch (and cache) basic + full metadata for a series.

        Cache resolution (skipped with ``force``): this package's split
        ``{id}_basic.json``/``_full.json`` first, then a ``bcb-sgs-fetcher``
        combined ``{id}.json`` (``catalogo sync``), then HTML scraping.
        """
        if not force:
            if storage.has_basic_metadata(self.data_dir, series_id):
                return self._build_metadata(
                    storage.read_basic_metadata(self.data_dir, series_id),
                    storage.read_full_metadata(self.data_dir, series_id),
                )
            combined = storage.read_combined_metadata(
                self.data_dir, series_id
            )
            if combined is not None:
                return self._build_metadata(
                    combined[BASIC], combined.get(FULL)
                )

        scraper = self._ensure_scraper()
        html = scraper.request_metadata_html(series_id)
        basic = parse_metadata_basic(html[BASIC])
        full = parse_metadata_full(html[FULL])
        storage.write_metadata(
            self.data_dir,
            series_id,
            basic=dataclasses.asdict(basic),
            full=dataclasses.asdict(full),
            html_basic=html[BASIC],
            html_full=html[FULL],
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

                new_records = [point_to_record(p) for p in points]
                latest = storage.latest_series_file(self.data_dir, series_id)
                old_records = (
                    storage.read_series_data(latest)
                    if latest is not None
                    else None
                )
                if old_records is None or not _records_equal(
                    old_records, new_records
                ):
                    storage.write_series_data(
                        self.data_dir, series_id, new_records
                    )
                else:
                    logger.info(
                        "Series %s unchanged; discarding download", series_id
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

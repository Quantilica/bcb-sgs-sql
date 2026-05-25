"""Integration tests for theme hierarchy and metadata upserts."""

import sqlalchemy as sa

from bcb_sgs_sql import database, models


def _theme_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(sa.func.count()).select_from(models.Theme)
        ).scalar_one()


def test_theme_hierarchy_idempotent(engine):
    with engine.begin() as conn:
        leaf1 = database.upsert_theme_hierarchy(conn, ["Preços", "Índices", "IPCA"])
    with engine.begin() as conn:
        leaf2 = database.upsert_theme_hierarchy(conn, ["Preços", "Índices", "IPCA"])
    assert leaf1 == leaf2
    assert _theme_count(engine) == 3


def test_sibling_chains_share_ancestors(engine):
    with engine.begin() as conn:
        database.upsert_theme_hierarchy(conn, ["Preços", "IPCA"])
        database.upsert_theme_hierarchy(conn, ["Preços", "IGP"])
    # "Preços" shared, two leaves → 3 rows total.
    assert _theme_count(engine) == 3


def test_empty_hierarchy_returns_none(engine):
    with engine.begin() as conn:
        assert database.upsert_theme_hierarchy(conn, None) is None
        assert database.upsert_theme_hierarchy(conn, []) is None


def test_save_series_metadata_upsert(engine):
    database.save_series_metadata(
        engine, [{"series_id": 433, "name": "IPCA", "frequency": "Mensal"}]
    )
    database.save_series_metadata(engine, [{"series_id": 433, "name": "IPCA novo"}])
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                models.SeriesMetadata.name, models.SeriesMetadata.frequency
            ).where(models.SeriesMetadata.series_id == 433)
        ).one()
    assert row.name == "IPCA novo"
    # Untouched column retains the prior value.
    assert row.frequency == "Mensal"


def test_metadata_links_theme(engine):
    with engine.begin() as conn:
        theme_id = database.upsert_theme_hierarchy(conn, ["Preços"])
    database.save_series_metadata(
        engine,
        [
            {
                "series_id": 1,
                "name": "X",
                "theme_id": theme_id,
                "theme_hierarchy": ["Preços"],
            }
        ],
    )
    with engine.connect() as conn:
        tid = conn.execute(
            sa.select(models.SeriesMetadata.theme_id).where(
                models.SeriesMetadata.series_id == 1
            )
        ).scalar_one()
    assert tid == theme_id

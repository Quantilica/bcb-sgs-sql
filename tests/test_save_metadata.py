"""Regression test for save_series_metadata's heterogeneous-batch upsert.

Requires a real PostgreSQL (uses the ``engine`` fixture); skipped without
``BCB_SGS_SQL_TEST_DSN``.
"""

import sqlalchemy as sa

from bcb_sgs_sql import database


def test_heterogeneous_batch_does_not_null_out_existing(engine):
    # Seed: series 1 with a name and a unit.
    database.save_series_metadata(
        engine, [{"series_id": 1, "name": "Série A", "unit": "%"}]
    )

    # Upsert a batch where series 1 updates `name` but OMITS `unit`, alongside
    # a second series that carries `unit`. The omitted `unit` must not overwrite
    # the stored value with NULL (COALESCE(excluded, current)).
    database.save_series_metadata(
        engine,
        [
            {"series_id": 1, "name": "Série A (rev)"},
            {"series_id": 2, "name": "Série B", "unit": "R$"},
        ],
    )

    with engine.connect() as conn:
        row1 = conn.execute(
            sa.text("SELECT name, unit FROM series_metadata WHERE series_id = 1")
        ).one()
        row2 = conn.execute(
            sa.text("SELECT name, unit FROM series_metadata WHERE series_id = 2")
        ).one()

    assert row1.name == "Série A (rev)"  # updated
    assert row1.unit == "%"  # preserved, NOT overwritten with NULL
    assert row2.name == "Série B"
    assert row2.unit == "R$"

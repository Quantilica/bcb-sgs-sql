"""Integration tests for the soft-versioned observation load.

These prove decision 4: history preserved without an audit table. They
require a real PostgreSQL (see conftest).
"""

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa

from bcb_sgs_sql import database, models

D1 = dt.date(2020, 1, 1)
D2 = dt.date(2020, 2, 1)


def _rows(engine, **where):
    stmt = sa.select(
        models.SeriesData.value, models.SeriesData.ativo
    ).order_by(models.SeriesData.loaded_at, models.SeriesData.id)
    for k, v in where.items():
        stmt = stmt.where(getattr(models.SeriesData, k) == v)
    with engine.connect() as conn:
        return [(r.value, r.ativo) for r in conn.execute(stmt)]


def _count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(sa.func.count()).select_from(models.SeriesData)
        ).scalar_one()


def test_first_load_all_active(engine):
    n_stg, n_ins, n_deact = database.load_series_data(
        engine,
        [(1, D1, None, Decimal("1.0")), (1, D2, None, Decimal("2.0"))],
    )
    assert (n_stg, n_ins, n_deact) == (2, 2, 0)
    assert _rows(engine, series_id=1, date=D1) == [(Decimal("1.0"), True)]
    assert _count(engine) == 2


def test_identical_reload_zero_churn(engine):
    rows = [(1, D1, None, Decimal("1.0"))]
    database.load_series_data(engine, rows)
    n_stg, n_ins, n_deact = database.load_series_data(engine, rows)
    assert (n_ins, n_deact) == (0, 0)
    assert _count(engine) == 1


def test_revision_inserts_new_and_deactivates_old(engine):
    database.load_series_data(engine, [(1, D1, None, Decimal("1.0"))])
    n_stg, n_ins, n_deact = database.load_series_data(
        engine, [(1, D1, None, Decimal("1.2"))]
    )
    assert (n_ins, n_deact) == (1, 1)
    # Full revision history preserved: old inactive, new active.
    assert _rows(engine, series_id=1, date=D1) == [
        (Decimal("1.0"), False),
        (Decimal("1.2"), True),
    ]


def test_only_one_active_per_key(engine):
    for v in ("1.0", "1.1", "1.2"):
        database.load_series_data(engine, [(1, D1, None, Decimal(v))])
    rows = _rows(engine, series_id=1, date=D1)
    assert len(rows) == 3
    assert [active for _, active in rows] == [False, False, True]
    assert rows[-1][0] == Decimal("1.2")


def test_null_date_end_treated_as_same_key(engine):
    database.load_series_data(engine, [(1, D1, None, Decimal("1.0"))])
    database.load_series_data(engine, [(1, D1, None, Decimal("9.0"))])
    # Same (series_id, date, NULL) key → revision, not a second active row.
    active = _rows(engine, series_id=1, date=D1)
    assert sum(1 for _, a in active if a) == 1


def test_values_only_load_creates_stub_metadata(engine):
    database.load_series_data(engine, [(42, D1, None, Decimal("1.0"))])
    with engine.connect() as conn:
        ids = [
            r.series_id
            for r in conn.execute(sa.select(models.SeriesMetadata.series_id))
        ]
    assert 42 in ids

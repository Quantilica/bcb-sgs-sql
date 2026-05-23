"""Shared pytest fixtures.

Integration tests need a real PostgreSQL (>= 15 for ``NULLS NOT
DISTINCT``). Provide a DSN via the ``BCB_SGS_SQL_TEST_DSN`` environment
variable, e.g.::

    postgresql+psycopg://user:pass@localhost:5432/dbname

If unset, or the connection fails, the DB-backed tests are skipped.
A dedicated throwaway schema is created and dropped per session.
"""

import os

import pytest
import sqlalchemy as sa

from bcb_sgs_sql import models

_TEST_SCHEMA = "bcb_sgs_sql_test"
_DSN = os.environ.get("BCB_SGS_SQL_TEST_DSN")


@pytest.fixture(scope="session")
def _engine():
    if not _DSN:
        pytest.skip("BCB_SGS_SQL_TEST_DSN not set")
    try:
        base = sa.create_engine(_DSN)
        with base.connect() as conn:
            conn.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
            )
            conn.execute(sa.text(f'CREATE SCHEMA "{_TEST_SCHEMA}"'))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {e}")

    engine = sa.create_engine(
        _DSN, connect_args={"options": f"-c search_path={_TEST_SCHEMA}"}
    )
    models.Base.metadata.create_all(engine)
    yield engine
    with base.connect() as conn:
        conn.execute(
            sa.text(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
        )
        conn.commit()
    engine.dispose()
    base.dispose()


@pytest.fixture
def engine(_engine):
    """Per-test engine with all tables truncated for isolation."""
    with _engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE series_data, arquivo_carregado, "
                "series_metadata, theme RESTART IDENTITY CASCADE"
            )
        )
    return _engine

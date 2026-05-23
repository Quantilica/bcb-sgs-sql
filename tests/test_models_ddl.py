"""DDL-level checks that don't need a database connection."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from bcb_sgs_sql import models

_DIALECT = postgresql.dialect()


def _ddl(table) -> str:
    parts = [str(CreateTable(table).compile(dialect=_DIALECT))]
    parts += [
        str(CreateIndex(ix).compile(dialect=_DIALECT)) for ix in table.indexes
    ]
    return "\n".join(parts)


def test_series_data_partial_unique_active_index():
    ddl = _ddl(models.SeriesData.__table__)
    assert "uq_series_data_active" in ddl
    assert "NULLS NOT DISTINCT" in ddl
    assert "WHERE ativo" in ddl


def test_series_metadata_computed_search_vector():
    ddl = _ddl(models.SeriesMetadata.__table__)
    assert "search_vector TSVECTOR GENERATED ALWAYS AS" in ddl
    assert "to_tsvector('portuguese'" in ddl
    assert "USING gin (theme_hierarchy)" in ddl


def test_theme_constraints():
    ddl = _ddl(models.Theme.__table__)
    assert "positive_level" in ddl
    assert "consistent_parent_level" in ddl
    assert "uq_theme UNIQUE NULLS NOT DISTINCT" in ddl


def test_series_metadata_pk_is_natural_series_id():
    pk = list(models.SeriesMetadata.__table__.primary_key.columns)
    assert [c.name for c in pk] == ["series_id"]
    assert pk[0].autoincrement is False

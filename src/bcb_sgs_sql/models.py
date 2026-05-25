"""SQLAlchemy ORM models for BCB SGS data in PostgreSQL.

Four tables:

* ``theme`` — self-referential theme hierarchy (level 1 = root).
* ``series_metadata`` — the series catalog (PK is the natural BCB
  ``series_id``); ``theme_hierarchy`` is kept both denormalized (ARRAY,
  GIN-indexed) and normalized (``theme_id`` FK).
* ``series_data`` — the fact table of observations, soft-versioned:
  revisions INSERT a new row and flip the prior one ``ativo = FALSE``
  instead of overwriting, so the full revision history is preserved
  without a separate audit table.  A partial unique index guarantees at
  most one ``ativo`` row per ``(series_id, date, date_end)``.
* ``arquivo_carregado`` — tracks loaded source files for idempotency.

Tables are unqualified; the schema is selected by the ``search_path`` set
in :func:`bcb_sgs_sql.database.get_engine`.
"""

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class Theme(Base):
    __tablename__ = "theme"
    __table_args__ = (
        CheckConstraint("level > 0", name="positive_level"),
        CheckConstraint(
            "(parent_id IS NULL AND level = 1)"
            " OR (parent_id IS NOT NULL AND level > 1)",
            name="consistent_parent_level",
        ),
        UniqueConstraint(
            "name",
            "level",
            "parent_id",
            name="uq_theme",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("theme.id"), nullable=True, index=True
    )
    children = relationship("Theme", backref=sa.orm.backref("parent", remote_side=[id]))


class SeriesMetadata(Base):
    __tablename__ = "series_metadata"
    __table_args__ = (
        sa.Index(
            "ix_series_metadata_theme_hierarchy_gin",
            "theme_hierarchy",
            postgresql_using="gin",
        ),
        sa.Index(
            "ix_series_metadata_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    series_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    name_index: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    name_abbreviated: Mapped[str | None] = mapped_column(Text)
    name_english: Mapped[str | None] = mapped_column(Text)
    name_english_abbreviated: Mapped[str | None] = mapped_column(Text)
    theme_hierarchy: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    frequency_acronym: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    last_date: Mapped[dt.date | None] = mapped_column(Date)
    last_date_index: Mapped[dt.date | None] = mapped_column(Date)
    series_type: Mapped[str | None] = mapped_column(Text)
    precision: Mapped[int | None] = mapped_column(Integer)
    max_value: Mapped[float | None] = mapped_column(Float)
    min_value: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool | None] = mapped_column(Boolean)
    special: Mapped[bool | None] = mapped_column(Boolean)
    formula: Mapped[str | None] = mapped_column(Text)
    series_primitive: Mapped[str | None] = mapped_column(Text)
    owner_manager: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    message_warning: Mapped[str | None] = mapped_column(Text)
    full_provider_data: Mapped[dict | None] = mapped_column(JSONB)
    full_description: Mapped[dict | None] = mapped_column(JSONB)
    full_methodology: Mapped[dict | None] = mapped_column(JSONB)
    full_dissemination_formats: Mapped[dict | None] = mapped_column(JSONB)
    last_update: Mapped[dt.date | None] = mapped_column(Date)
    theme_id: Mapped[int | None] = mapped_column(
        ForeignKey("theme.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('portuguese',"
            " coalesce(name, '') || ' '"
            " || coalesce(name_abbreviated, '') || ' '"
            " || coalesce(unit, '') || ' '"
            " || coalesce(source, '') || ' '"
            " || coalesce(name_english, '') || ' '"
            " || coalesce(series_id::text, ''))",
            persisted=True,
        ),
    )

    data = relationship("SeriesData", back_populates="series_metadata")


class SeriesData(Base):
    __tablename__ = "series_data"
    __table_args__ = (
        # At most one active row per (series_id, date, date_end). NULL
        # date_end values collide (nulls_not_distinct) so a daily series
        # cannot have two active observations on the same date.
        sa.Index(
            "uq_series_data_active",
            "series_id",
            "date",
            "date_end",
            unique=True,
            postgresql_where=sa.text("ativo"),
            postgresql_nulls_not_distinct=True,
        ),
        sa.Index("ix_series_data_series_id", "series_id"),
        sa.Index(
            "ix_series_data_active_lookup",
            "series_id",
            "date",
            postgresql_where=sa.text("ativo"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series_metadata.series_id"),
        nullable=False,
    )
    series_metadata = relationship("SeriesMetadata", back_populates="data")
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    date_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    loaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ArquivoCarregado(Base):
    __tablename__ = "arquivo_carregado"

    arquivo: Mapped[str] = mapped_column(Text, primary_key=True)
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series_metadata.series_id"),
        nullable=True,
        index=True,
    )
    carregado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

import datetime as dt
from decimal import Decimal

import polars as pl

from bcb_sgs_sql import loader


def test_record_to_tuple_parses_types():
    rec = {
        "series_id": "433",
        "date": "2020-01-01",
        "value": "1.25",
        "date_end": "2020-01-31",
    }
    sid, d, de, v = loader.record_to_tuple(rec)
    assert sid == 433
    assert d == dt.date(2020, 1, 1)
    assert de == dt.date(2020, 1, 31)
    assert v == Decimal("1.25")


def test_record_to_tuple_handles_nulls():
    sid, d, de, v = loader.record_to_tuple(
        {"series_id": 1, "date": "2020-01-01", "value": None}
    )
    assert de is None
    assert v is None


def test_basic_to_metadata_row_field_mapping():
    basic = {
        "series_id": 433,
        "name": "IPCA",
        "manager_owner": "Depec",
        "primitive_series": "1,2",
        "warning_message": "cuidado",
        "theme_hierarchy": ["Preços"],
        "start_date": "1980-01-01",
        "end_date": "2020-12-01",
    }
    full = {"last_update": "2021-01-01", "provider_data": [{"a": 1}]}
    row = loader.basic_to_metadata_row(
        basic, full, frequency_acronym="M", theme_id=7
    )
    assert row["series_id"] == 433
    assert row["owner_manager"] == "Depec"
    assert row["series_primitive"] == "1,2"
    assert row["message_warning"] == "cuidado"
    assert row["last_date"] == dt.date(2020, 12, 1)
    assert row["frequency_acronym"] == "M"
    assert row["theme_id"] == 7
    assert row["last_update"] == dt.date(2021, 1, 1)
    assert row["full_provider_data"] == [{"a": 1}]


def test_read_parquet_rows(tmp_path):
    df = pl.DataFrame(
        {
            "series_id": [1, 1],
            "date": [dt.date(2020, 1, 1), dt.date(2020, 2, 1)],
            "value": [1.5, 2.5],
            "date_end": [None, None],
        },
        schema={
            "series_id": pl.Int64,
            "date": pl.Date,
            "value": pl.Float64,
            "date_end": pl.Date,
        },
    )
    p = tmp_path / "s.parquet"
    df.write_parquet(p)
    rows = loader.read_parquet_rows(p)
    assert len(rows) == 2
    assert rows[0][0] == 1
    assert rows[0][1] == dt.date(2020, 1, 1)
    assert rows[0][3] == Decimal("1.5")


def test_classify(tmp_path):
    (tmp_path / "000433_basic.json").write_text("{}")
    assert loader._classify(tmp_path) == "metadata"

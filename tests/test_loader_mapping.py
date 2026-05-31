import datetime as dt
import json
from decimal import Decimal

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
    row = loader.basic_to_metadata_row(basic, full, frequency_acronym="M", theme_id=7)
    assert row["series_id"] == 433
    assert row["owner_manager"] == "Depec"
    assert row["series_primitive"] == "1,2"
    assert row["message_warning"] == "cuidado"
    assert row["last_date"] == dt.date(2020, 12, 1)
    assert row["frequency_acronym"] == "M"
    assert row["theme_id"] == 7
    assert row["last_update"] == dt.date(2021, 1, 1)
    assert row["full_provider_data"] == [{"a": 1}]


def test_read_json_rows(tmp_path):
    records = [
        {"series_id": 1, "date": "2020-01-01", "value": "1.5", "date_end": None},
        {"series_id": 1, "date": "2020-02-01", "value": "2.5", "date_end": None},
    ]
    p = tmp_path / "series.json"
    p.write_text(json.dumps(records))
    rows = loader.read_json_rows(p)
    assert len(rows) == 2
    assert rows[0][0] == 1
    assert rows[0][1] == dt.date(2020, 1, 1)
    assert rows[0][3] == Decimal("1.5")


def test_classify_split(tmp_path):
    (tmp_path / "000433_basic.json").write_text("{}")
    assert loader._classify(tmp_path) == "metadata"


def _write_combined(path, series_id, basic=None, full=None):
    payload = {
        "basic": {"series_id": series_id, "name": f"S{series_id}", **(basic or {})},
        "full": full,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_iter_metadata_reads_combined(tmp_path):
    _write_combined(
        tmp_path / "000001.json",
        1,
        basic={"frequency": "Diária", "theme_hierarchy": ["A", "B"]},
        full={"last_update": "2021-01-01"},
    )
    pairs = list(loader.iter_metadata(tmp_path))
    assert len(pairs) == 1
    basic, full = pairs[0]
    assert basic["series_id"] == 1
    assert basic["theme_hierarchy"] == ["A", "B"]
    assert full["last_update"] == "2021-01-01"


def test_iter_metadata_reads_split(tmp_path):
    (tmp_path / "000002_basic.json").write_text(
        json.dumps({"series_id": 2, "name": "S2"})
    )
    (tmp_path / "000002_full.json").write_text(
        json.dumps({"last_update": "2020-01-01"})
    )
    pairs = list(loader.iter_metadata(tmp_path))
    assert len(pairs) == 1
    basic, full = pairs[0]
    assert basic["series_id"] == 2
    assert full["last_update"] == "2020-01-01"


def test_iter_metadata_ignores_observation_lists(tmp_path):
    (tmp_path / "999999.json").write_text(
        json.dumps([{"series_id": 1, "date": "2020-01-01", "value": "1"}])
    )
    assert list(loader.iter_metadata(tmp_path)) == []


def test_classify_combined(tmp_path):
    _write_combined(tmp_path / "000001.json", 1)
    assert loader._classify(tmp_path) == "metadata"


def test_classify_observations(tmp_path):
    (tmp_path / "series_1@20200101T000000.json").write_text(
        json.dumps([{"series_id": 1, "date": "2020-01-01", "value": "1"}])
    )
    assert loader._classify(tmp_path) == "json"


def test_resolve_metadata_dir_month_partition(tmp_path):
    meta = tmp_path / "bcb-sgs_2026-05" / "metadata"
    meta.mkdir(parents=True)
    _write_combined(meta / "000001.json", 1)
    assert loader._resolve_metadata_dir(tmp_path) == meta


def test_resolve_metadata_dir_subdir(tmp_path):
    meta = tmp_path / "metadata"
    meta.mkdir()
    _write_combined(meta / "000001.json", 1)
    assert loader._resolve_metadata_dir(tmp_path) == meta


def test_resolve_data_dir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert loader._resolve_data_dir(tmp_path) == data
    assert loader._resolve_data_dir(tmp_path / "missing") is None


def test_freq_acronym_reexported_from_toml_runner():
    from bcb_sgs_sql.toml_runner import _freq_acronym

    assert _freq_acronym("Diária") == "D"
    assert _freq_acronym("Mensal") == "M"
    assert _freq_acronym(None) is None

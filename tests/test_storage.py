from bcb_sgs_sql.storage import Storage


def test_build_series_filename_padding_and_stamp():
    name = Storage.build_series_filename(433, "20260101T120000")
    assert name == "series-000433@20260101T120000.json"


def test_series_roundtrip(tmp_path):
    s = Storage(tmp_path)
    rows = [
        {"series_id": 1, "date": "2020-01-01", "value": "1.5",
         "date_end": None},
        {"series_id": 1, "date": "2020-02-01", "value": "2.5",
         "date_end": None},
    ]
    path = s.write_series_data(rows, 1, "20260101T000000")
    assert path.exists()
    assert s.read_series_data(path) == rows


def test_read_series_dir_keeps_latest_stamp(tmp_path):
    s = Storage(tmp_path)
    s.write_series_data([{"a": 1}], 7, "20260101T000000")
    s.write_series_data([{"a": 2}], 7, "20260201T000000")
    s.write_series_data([{"a": 3}], 9, "20260101T000000")
    files = s.read_series_dir()
    names = sorted(f.name for f in files)
    assert names == [
        "series-000007@20260201T000000.json",
        "series-000009@20260101T000000.json",
    ]


def test_metadata_roundtrip(tmp_path):
    s = Storage(tmp_path)
    assert s.has_basic_metadata(5) is False
    s.write_metadata(5, basic={"series_id": 5, "name": "X"}, full=None)
    assert s.has_basic_metadata(5) is True
    assert s.read_basic_metadata(5) == {"series_id": 5, "name": "X"}
    assert s.read_full_metadata(5) is None

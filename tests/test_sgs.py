import pytest

from bcb_sgs_sql.sgs import Fetcher
from bcb_sgs_sql.toml_runner import _freq_acronym


def _fetcher(tmp_path):
    return Fetcher(tmp_path)


def test_plan_series_ids(tmp_path):
    f = _fetcher(tmp_path)
    ids = f.plan_series([{"ids": [433, 13522]}, {"ids": [11]}])
    assert ids == [11, 433, 13522]


def test_plan_series_dedups_and_sorts(tmp_path):
    f = _fetcher(tmp_path)
    assert f.plan_series([{"ids": [5, 5, 1]}]) == [1, 5]


def test_plan_series_themes_without_engine_raises(tmp_path):
    f = _fetcher(tmp_path)
    with pytest.raises(ValueError, match="populated catalog"):
        f.plan_series([{"themes": ["Preços"]}])


def test_plan_series_requires_selector(tmp_path):
    f = _fetcher(tmp_path)
    with pytest.raises(ValueError, match="ids' or 'themes"):
        f.plan_series([{"frequency": "M"}])


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Diária", "D"),
        ("Mensal", "M"),
        ("ANUAL", "A"),
        ("trimestral", "T"),
        ("Quadrimestral", "Qd"),
        ("Semanal", "S"),
        (None, None),
        ("desconhecida", None),
    ],
)
def test_freq_acronym(label, expected):
    assert _freq_acronym(label) == expected

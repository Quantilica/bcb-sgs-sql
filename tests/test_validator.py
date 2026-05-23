from bcb_sgs_sql.validator import PluginValidator


def _make_plugin(root, fetch=None, transform=None, sql_files=None):
    (root / "manifest.toml").write_text(
        'name = "p"\nversion = "1.0.0"\n\n'
        '[[pipeline]]\nid = "pipe"\ndescription = "d"\npath = "pipe"\n',
        encoding="utf-8",
    )
    pipe = root / "pipe"
    pipe.mkdir()
    if fetch is not None:
        (pipe / "fetch.toml").write_text(fetch, encoding="utf-8")
    if transform is not None:
        (pipe / "transform.toml").write_text(transform, encoding="utf-8")
    for name, content in (sql_files or {}).items():
        (pipe / name).write_text(content, encoding="utf-8")
    return root


def test_valid_fetch_series(tmp_path):
    _make_plugin(tmp_path, fetch="[[series]]\nids = [433]\n")
    report = PluginValidator(tmp_path).validate()
    assert report.is_valid


def test_fetch_series_requires_ids_or_themes(tmp_path):
    _make_plugin(tmp_path, fetch="[[series]]\nfrequency = \"M\"\n")
    report = PluginValidator(tmp_path).validate()
    assert not report.is_valid
    msgs = [
        i.message
        for s in report.sections
        for i in s.issues
    ]
    assert any("ids" in m and "themes" in m for m in msgs)


def test_themes_selector_is_valid(tmp_path):
    _make_plugin(
        tmp_path, fetch='[[series]]\nthemes = ["Preços"]\nfrequency="M"\n'
    )
    report = PluginValidator(tmp_path).validate()
    assert report.is_valid


def test_transform_missing_sql_file_errors(tmp_path):
    _make_plugin(
        tmp_path,
        transform=(
            '[[table]]\nname="x"\nschema="a"\nstrategy="view"\n'
            'sql="missing.sql"\n'
        ),
    )
    report = PluginValidator(tmp_path).validate()
    assert not report.is_valid


def test_transform_valid(tmp_path):
    _make_plugin(
        tmp_path,
        transform=(
            '[[table]]\nname="x"\nschema="a"\nstrategy="replace"\n'
            'sql="x.sql"\n'
        ),
        sql_files={"x.sql": "SELECT 1"},
    )
    report = PluginValidator(tmp_path).validate()
    assert report.is_valid

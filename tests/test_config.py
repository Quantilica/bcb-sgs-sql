import configparser
from pathlib import Path

import pytest

from bcb_sgs_sql import config as config_mod
from bcb_sgs_sql.config import Config, ConfigError


def _write_ini(path: Path, data: dict) -> None:
    cfg = configparser.ConfigParser()
    for section, opts in data.items():
        cfg[section] = opts
    with open(path, "w") as f:
        cfg.write(f)


@pytest.fixture
def isolate_config(tmp_path, monkeypatch):
    """Point both global and local config paths at a temp dir."""
    global_ini = tmp_path / "global.ini"
    local_ini = tmp_path / "local.ini"
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", global_ini)
    monkeypatch.setattr(config_mod, "LOCAL_CONFIG_PATH", local_ini)
    return global_ini, local_ini


def _full_db(**over) -> dict:
    base = {
        "user": "u",
        "password": "p",
        "host": "h",
        "port": "5432",
        "dbname": "db",
        "schema": "s",
    }
    base.update(over)
    return base


def test_missing_all_raises_setup_hint(isolate_config):
    with pytest.raises(ConfigError) as exc:
        Config()
    assert "No configuration found" in str(exc.value)


def test_missing_some_keys_lists_them(isolate_config):
    global_ini, _ = isolate_config
    _write_ini(global_ini, {"database": {"user": "u"}})
    with pytest.raises(ConfigError) as exc:
        Config()
    assert "database.host" in str(exc.value)
    assert "storage.data_dir" in str(exc.value)


def test_valid_config_loads_fields(isolate_config):
    global_ini, _ = isolate_config
    _write_ini(
        global_ini,
        {"database": _full_db(), "storage": {"data_dir": "/tmp/x"}},
    )
    cfg = Config()
    assert cfg.db_user == "u"
    assert cfg.db_name == "db"
    assert cfg.db_schema == "s"
    assert str(cfg.data_dir) == str(Path("/tmp/x"))


def test_local_overrides_global(isolate_config):
    global_ini, local_ini = isolate_config
    _write_ini(
        global_ini,
        {
            "database": _full_db(host="global-host"),
            "storage": {"data_dir": "/g"},
        },
    )
    _write_ini(local_ini, {"database": {"host": "local-host"}})
    cfg = Config()
    assert cfg.db_host == "local-host"

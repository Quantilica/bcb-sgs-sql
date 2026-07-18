"""CLI regression tests (no database required)."""

import pytest
from typer.testing import CliRunner

from bcb_sgs_sql import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_bootstrap(monkeypatch):
    # The Typer @app.callback() clones the default plugin via git; stub it out.
    monkeypatch.setattr(cli.manager, "ensure_defaults", lambda: None)


def _boom(*args, **kwargs):
    raise RuntimeError("boom")


@pytest.mark.parametrize(
    "command",
    [
        ["run", "std", "precos"],
        ["transform", "std", "precos"],
    ],
)
def test_run_and_transform_exit_nonzero_on_failure(monkeypatch, command):
    # A failure inside the command must surface as a non-zero exit code so CI
    # and automation can detect it (regression guard: run/transform used to
    # swallow exceptions and exit 0).
    monkeypatch.setattr(cli, "Config", _boom)
    result = runner.invoke(cli.app, command)
    assert result.exit_code != 0

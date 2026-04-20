"""Smoke tests for the Typer CLI: dispatch, side guard, stubs, --version."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from solx import __version__
from solx.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_sol(monkeypatch):
    """Force side.detect() to report 'sol'."""
    from solx import side

    monkeypatch.setattr(side, "detect", lambda **_: "sol")


@pytest.fixture
def fake_not_sol(monkeypatch):
    """Force side.detect() to report 'not-sol'."""
    from solx import side

    monkeypatch.setattr(side, "detect", lambda **_: "not-sol")


def test_version_flag(runner: CliRunner):
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_where_reports_sol(runner: CliRunner, fake_sol):
    result = runner.invoke(app, ["where"])
    assert result.exit_code == 0
    assert "sol mode" in result.stdout


def test_where_reports_not_sol(runner: CliRunner, fake_not_sol):
    result = runner.invoke(app, ["where"])
    assert result.exit_code == 0
    assert "not-sol mode" in result.stdout


def test_session_info_blocked_off_sol(runner: CliRunner, fake_not_sol):
    result = runner.invoke(app, ["session", "info"])
    assert result.exit_code == 2
    assert "Sol only" in result.stderr or "ssh to Sol" in result.stderr.lower()


def test_config_show_blocked_off_sol(runner: CliRunner, fake_not_sol):
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["init"],
        ["up"],
        ["up", "gpu"],
        ["down"],
        ["forward", "8888"],
        ["info"],
    ],
)
def test_laptop_stubs_exit_2_with_deferred_message(
    runner: CliRunner, fake_sol, argv: list[str]
):
    # Even on Sol, the laptop-side stubs refuse to run.
    result = runner.invoke(app, argv)
    assert result.exit_code == 2
    assert "deferred" in result.stderr.lower()


def test_session_start_dispatches_to_sol_cmds(
    runner: CliRunner, fake_sol, monkeypatch, tmp_path
):
    from solx import sol_cmds

    captured: dict = {}

    def fake_session_start(
        profile_name="default",
        *,
        dry_run=False,
        passthrough=None,
        **kwargs,
    ):
        captured["profile_name"] = profile_name
        captured["dry_run"] = dry_run
        captured["passthrough"] = passthrough
        return 0

    monkeypatch.setattr(sol_cmds, "session_start", fake_session_start)

    result = runner.invoke(
        app,
        ["session", "start", "gpu", "--dry-run", "--", "--mem=128G"],
    )
    assert result.exit_code == 0
    assert captured["profile_name"] == "gpu"
    assert captured["dry_run"] is True
    assert "--mem=128G" in (captured["passthrough"] or [])


def test_session_info_json_dispatches(runner: CliRunner, fake_sol, monkeypatch):
    from solx import sol_cmds

    seen: dict = {}

    def fake_info(*, json_output=False, session_path=None):
        seen["json_output"] = json_output
        return 0

    monkeypatch.setattr(sol_cmds, "session_info", fake_info)
    result = runner.invoke(app, ["session", "info", "--json"])
    assert result.exit_code == 0
    assert seen["json_output"] is True

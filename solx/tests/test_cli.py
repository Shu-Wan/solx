"""CLI dispatch + alias coverage via Typer's CliRunner.

These tests verify the wiring (subcommand routing, alias paths, flag
parsing). Behavior of each command body is tested in test_jobs.py /
test_keep.py / test_init.py / test_config.py. We mock `require_sol` here
so the suite passes off-Sol.
"""
from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from solx import cli
from solx import config as cfg
from solx import side


@pytest.fixture(autouse=True)
def _force_on_sol(monkeypatch):
    """Skip the side guard so every test runs as if on Sol."""
    monkeypatch.setattr(side, "require_sol", lambda: None)
    monkeypatch.setattr(cli, "require_sol", lambda: None)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- top level ----------------------------------------------------------


def test_version(runner: CliRunner) -> None:
    from solx import __version__

    res = runner.invoke(cli.app, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.stdout


def test_help_lists_commands(runner: CliRunner) -> None:
    res = runner.invoke(cli.app, ["--help"])
    assert res.exit_code == 0
    for cmd in ("init", "keep", "jump", "job", "jobs", "config", "completions"):
        assert cmd in res.stdout


def test_version_subcommand_aliases_flag(runner: CliRunner) -> None:
    """`solx version` matches `solx --version`."""
    from solx import __version__

    res = runner.invoke(cli.app, ["version"])
    assert res.exit_code == 0
    assert __version__ in res.stdout


def test_help_subcommand_aliases_flag(runner: CliRunner) -> None:
    """`solx help` shows the root help, same as `solx --help`."""
    res = runner.invoke(cli.app, ["help"])
    assert res.exit_code == 0
    for cmd in ("init", "keep", "job", "config", "completions"):
        assert cmd in res.stdout


# ---- alias coverage -----------------------------------------------------


def test_jobs_alias_routes_to_job_group(runner: CliRunner, monkeypatch) -> None:
    """`solx jobs list` should dispatch the same as `solx job list`."""
    called: list[str] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod, "cmd_list",
        lambda **kw: called.append("list") or 0,
    )
    res = runner.invoke(cli.app, ["jobs", "list"])
    assert res.exit_code == 0
    assert called == ["list"]


def test_ls_alias(runner: CliRunner, monkeypatch) -> None:
    called: list[str] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod, "cmd_list",
        lambda **kw: called.append("list") or 0,
    )
    res = runner.invoke(cli.app, ["job", "ls"])
    assert res.exit_code == 0
    assert called == ["list"]


def test_top_level_jump_routes_to_job_jump(runner: CliRunner, monkeypatch) -> None:
    """`solx jump` should run the same body as `solx job jump`."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod
    from solx.config import Config, JobTemplate

    monkeypatch.setattr(
        jobs_mod, "cmd_jump",
        lambda **kw: captured.append(kw) or 0,
    )
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(cli.app, ["jump", "12345", "--quiet"])
    assert res.exit_code == 0
    assert captured[0]["jobid_arg"] == "12345"
    assert captured[0]["quiet"] is True


# ---- global output flags ------------------------------------------------


def test_global_json_forces_json(runner: CliRunner, monkeypatch) -> None:
    import json as _json
    from solx.config import Config, JobTemplate

    fake_config = Config(
        default_shell="zsh", default_template="default", start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="lightwork", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)
    # global --json before the subcommand; config show has no local --json here
    res = runner.invoke(cli.app, ["--json", "config", "show"])
    assert res.exit_code == 0
    assert _json.loads(res.stdout)["default_shell"] == "zsh"


# ---- job subcommands ----------------------------------------------------


def test_job_start_passthrough(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod
    from solx.config import Config, JobTemplate

    monkeypatch.setattr(
        jobs_mod, "cmd_start",
        lambda **kw: captured.append(kw) or 0,
    )
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(
        cli.app,
        ["job", "start", "default", "--", "--mem=128G"],
    )
    assert res.exit_code == 0
    assert captured[0]["template_name"] == "default"
    assert captured[0]["passthrough"] == ["--mem=128G"]


def test_job_start_dry_run_flag(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod
    from solx.config import Config, JobTemplate

    monkeypatch.setattr(
        jobs_mod, "cmd_start",
        lambda **kw: captured.append(kw) or 0,
    )
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(cli.app, ["job", "start", "--dry-run"])
    assert res.exit_code == 0
    assert captured[0]["dry_run"] is True


def test_job_start_timeout_override(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod
    from solx.config import Config, JobTemplate

    monkeypatch.setattr(
        jobs_mod, "cmd_start",
        lambda **kw: captured.append(kw) or 0,
    )
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(cli.app, ["job", "start", "--timeout", "5m"])
    assert res.exit_code == 0
    assert captured[0]["timeout_override"] == 300


def test_job_start_invalid_timeout(runner: CliRunner, monkeypatch) -> None:
    from solx.config import Config, JobTemplate

    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(cli.app, ["job", "start", "--timeout", "never"])
    assert res.exit_code == 2


def test_job_stop_yes_flag(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod, "cmd_stop",
        lambda **kw: captured.append(kw) or 0,
    )
    res = runner.invoke(cli.app, ["job", "stop", "12345", "-y"])
    assert res.exit_code == 0
    assert captured[0]["yes"] is True
    assert captured[0]["dry_run"] is False


def test_job_stop_force_is_alias_for_yes(runner: CliRunner, monkeypatch) -> None:
    """`-f`/`--force` is interchangeable with `-y`/`--yes` for skipping the prompt."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_stop", lambda **kw: captured.append(kw) or 0)
    res = runner.invoke(cli.app, ["job", "stop", "12345", "--force"])
    assert res.exit_code == 0
    assert captured[0]["yes"] is True


def test_job_time_no_arg(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(
        jobs_mod, "cmd_time",
        lambda **kw: captured.append(kw) or 0,
    )
    res = runner.invoke(cli.app, ["job", "time"])
    assert res.exit_code == 0
    assert captured[0]["jobid_arg"] is None


# ---- keep ---------------------------------------------------------------


def test_keep_dry_run(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import keep as keep_mod
    from solx.config import Config, JobTemplate

    monkeypatch.setattr(
        keep_mod, "cmd_keep",
        lambda **kw: captured.append(kw) or 0,
    )
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(cli.app, ["keep", "-n"])
    assert res.exit_code == 0
    assert captured[0]["dry_run"] is True


def test_keep_invalid_stage(runner: CliRunner, monkeypatch) -> None:
    res = runner.invoke(cli.app, ["keep", "--stage", "bogus"])
    assert res.exit_code == 2
    assert "invalid --stage" in res.stderr or "invalid --stage" in res.stdout


def test_keep_solkeep_flag_and_missing_config(runner: CliRunner, monkeypatch, tmp_path) -> None:
    """`solx keep --solkeep ...` works with no config.toml (config passed as None)."""
    captured: list[dict] = []
    from solx import keep as keep_mod

    monkeypatch.setattr(keep_mod, "cmd_keep", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "absent.toml")
    res = runner.invoke(cli.app, ["keep", "--solkeep", "/tmp/mk", "-y"])
    assert res.exit_code == 0
    assert str(captured[0]["solkeep"]) == "/tmp/mk"
    assert captured[0]["config"] is None  # missing config tolerated for keep


def test_keep_full_flag_set(runner: CliRunner, monkeypatch, tmp_path) -> None:
    captured: list[dict] = []
    from solx import keep as keep_mod
    from solx.config import Config, JobTemplate

    monkeypatch.setattr(
        keep_mod, "cmd_keep",
        lambda **kw: captured.append(kw) or 0,
    )
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)

    res = runner.invoke(
        cli.app,
        [
            "keep",
            "--stage", "pending",
            "--csv-dir", str(tmp_path),
            "-j", "4",
            "-y",
            "-v",
        ],
    )
    assert res.exit_code == 0
    kw = captured[0]
    assert kw["stage"] == "pending"
    assert kw["csv_dir"] == tmp_path
    assert kw["jobs_n"] == 4
    assert kw["yes"] is True
    assert kw["verbose"] is True


# ---- init ---------------------------------------------------------------


def test_init_default(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(
        init_mod, "cmd_init",
        lambda **kw: captured.append(kw) or 0,
    )
    res = runner.invoke(cli.app, ["init"])
    assert res.exit_code == 0
    assert captured[0]["force"] is False


def test_init_force(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(
        init_mod, "cmd_init",
        lambda **kw: captured.append(kw) or 0,
    )
    res = runner.invoke(cli.app, ["init", "-f"])
    assert res.exit_code == 0
    assert captured[0]["force"] is True


def test_init_yes_is_alias_for_force(runner: CliRunner, monkeypatch) -> None:
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(init_mod, "cmd_init", lambda **kw: captured.append(kw) or 0)
    res = runner.invoke(cli.app, ["init", "-y"])
    assert res.exit_code == 0
    assert captured[0]["force"] is True


# ---- config -------------------------------------------------------------


def test_config_show(runner: CliRunner, monkeypatch) -> None:
    from solx.config import Config, JobTemplate
    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={
            "default": JobTemplate(
                name="default", partition="lightwork", time="1-0",
                qos="public",
            )
        },
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)
    res = runner.invoke(cli.app, ["config", "show"])
    assert res.exit_code == 0
    assert "lightwork" in res.stdout


def test_config_show_json(runner: CliRunner, monkeypatch) -> None:
    from solx.config import Config, JobTemplate
    import json

    fake_config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={
            "default": JobTemplate(
                name="default", partition="lightwork", time="1-0"
            )
        },
    )
    monkeypatch.setattr(cli, "_load_or_exit", lambda *a, **kw: fake_config)
    res = runner.invoke(cli.app, ["config", "show", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["default_shell"] == "zsh"
    assert "default" in data["templates"]


def test_config_edit_no_config(runner: CliRunner, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "absent.toml")
    res = runner.invoke(cli.app, ["config", "edit"])
    assert res.exit_code == 2


def test_config_import_solkeep_wiring(runner: CliRunner, monkeypatch) -> None:
    """`solx config import-solkeep --solkeep F` routes to init.cmd_import_solkeep."""
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(
        init_mod, "cmd_import_solkeep", lambda **kw: captured.append(kw) or 0
    )
    res = runner.invoke(cli.app, ["config", "import-solkeep", "--solkeep", "/tmp/mk"])
    assert res.exit_code == 0
    assert str(captured[0]["solkeep"]) == "/tmp/mk"


# ---- completions --------------------------------------------------------


def test_completions_invalid_shell(runner: CliRunner) -> None:
    res = runner.invoke(cli.app, ["completions", "tcsh"])
    assert res.exit_code == 2


def test_completions_bash_emits_script(runner: CliRunner) -> None:
    """Happy path: a real completion script, generated without re-exec."""
    res = runner.invoke(cli.app, ["completions", "bash"])
    assert res.exit_code == 0
    assert "_SOLX_COMPLETE" in res.stdout  # the env var the script wires up
    assert "solx" in res.stdout
    assert "loadautofunc" not in res.stdout  # zsh-only post-processing


def test_completions_zsh_emits_script(runner: CliRunner) -> None:
    """zsh emits a valid `#compdef` script wired to Typer's runtime handler."""
    res = runner.invoke(cli.app, ["completions", "zsh"])
    assert res.exit_code == 0
    assert "#compdef solx" in res.stdout
    assert "_SOLX_COMPLETE=complete_zsh" in res.stdout
    assert "_TYPER_COMPLETE_ARGS" in res.stdout


def test_completions_zsh_dual_mode_footer(runner: CliRunner) -> None:
    """The zsh script supports both install modes: autoloaded from fpath
    (the `loadautofunc` branch calls the completer, so the first Tab of a
    session completes) and eval/source (compdef registers it)."""
    res = runner.invoke(cli.app, ["completions", "zsh"])
    assert res.exit_code == 0
    assert "loadautofunc" in res.stdout
    assert '_solx_completion "$@"' in res.stdout
    # the compdef only survives inside the else-branch (indented) — a bare
    # column-0 compdef would make the script eval-only again
    assert "compdef _solx_completion solx" not in res.stdout.splitlines()
    assert res.stdout.rstrip().endswith("fi")


def test_zsh_dual_mode_fallback_unmodified() -> None:
    """A script without the expected trailing compdef (Typer template
    changed) passes through untouched instead of being mangled."""
    for script in (
        "#compdef solx\n_new_typer_footer solx",  # no compdef line at all
        "compdef _solx_completion solx\nmore lines after",  # not trailing
    ):
        assert cli._zsh_dual_mode(script) == script


def test_runtime_completion_dispatch_resolves_shell(monkeypatch, capsys) -> None:
    """The emitted script calls `_SOLX_COMPLETE=complete_zsh solx` at runtime;
    that path must resolve the shell (subcommands), not "Shell zsh not
    supported" — i.e. `completion_init()` ran at import despite
    add_completion=False.
    """
    from typer.completion import shell_complete
    from typer.main import get_command

    monkeypatch.setenv("_TYPER_COMPLETE_ARGS", "solx ")
    rc = shell_complete(
        get_command(cli.app), {}, "solx", "_SOLX_COMPLETE", "complete_zsh"
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "not supported" not in (captured.out + captured.err).lower()
    assert "init" in captured.out  # top-level subcommands are completed


def test_config_edit_splits_editor_flags(runner: CliRunner, monkeypatch, tmp_path) -> None:
    """$EDITOR with flags (e.g. `code --wait`) is split into argv, not one binary."""
    cfgfile = tmp_path / "config.toml"
    cfgfile.write_text("default_shell = 'bash'\n")
    monkeypatch.setattr(cfg, "config_path", lambda: cfgfile)
    monkeypatch.setenv("EDITOR", "myed --wait")
    captured: dict = {}

    def fake_call(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    res = runner.invoke(cli.app, ["config", "edit"])
    assert res.exit_code == 0
    assert captured["argv"] == ["myed", "--wait", str(cfgfile)]

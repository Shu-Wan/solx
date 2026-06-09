"""CLI dispatch + alias coverage for the argparse entry point.

These tests verify the wiring (subcommand routing, alias paths, flag
parsing). Behavior of each command body is tested in test_jobs.py /
test_keep.py / test_init.py / test_config.py. We mock `require_sol` here
so the suite passes off-Sol.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from solx import __version__
from solx import config as cfg
from solx import main as main_mod
from solx import side
from solx.config import Config, JobTemplate


@pytest.fixture(autouse=True)
def _force_on_sol(monkeypatch):
    """Skip the side guard so every test runs as if on Sol."""
    monkeypatch.setattr(side, "require_sol", lambda: None)


def invoke(argv: list[str]) -> int:
    """Run main(argv) and return the exit code carried by SystemExit."""
    with pytest.raises(SystemExit) as excinfo:
        main_mod.main(argv)
    code = excinfo.value.code
    return 0 if code is None else int(code)


def fake_config() -> Config:
    return Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": JobTemplate(name="default", partition="x", time="1-0")},
    )


# ---- top level ----------------------------------------------------------


def test_version(capsys) -> None:
    assert invoke(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_version_flag_after_other_root_options(capsys) -> None:
    assert invoke(["--json", "--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_help_lists_commands(capsys) -> None:
    assert invoke(["--help"]) == 0
    out = capsys.readouterr().out
    for cmd in ("init", "keep", "jump", "job", "jobs", "config", "completions"):
        assert cmd in out


def test_version_subcommand_aliases_flag(capsys) -> None:
    """`solx version` matches `solx --version`."""
    assert invoke(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_help_subcommand_aliases_flag(capsys) -> None:
    """`solx help` shows the root help, same as `solx --help`."""
    assert invoke(["help"]) == 0
    out = capsys.readouterr().out
    for cmd in ("init", "keep", "job", "config", "completions"):
        assert cmd in out


def test_no_args_prints_help_and_exits_2(capsys) -> None:
    assert invoke([]) == 2
    assert "usage: solx" in capsys.readouterr().out


def test_job_group_no_args_prints_help_and_exits_2(capsys) -> None:
    assert invoke(["job"]) == 2
    assert "usage: solx job" in capsys.readouterr().out


def test_unknown_command_exits_2(capsys) -> None:
    assert invoke(["frobnicate"]) == 2
    assert "usage" in capsys.readouterr().err


def test_no_option_abbreviation(monkeypatch, capsys) -> None:
    """Option prefixes are never expanded (`--dry` must not match --dry-run)."""
    from solx import keep as keep_mod

    monkeypatch.setattr(keep_mod, "cmd_keep", lambda **kw: 0)
    assert invoke(["keep", "--dry"]) == 2


# ---- alias coverage -----------------------------------------------------


def test_jobs_alias_routes_to_job_group(monkeypatch) -> None:
    """`solx jobs list` should dispatch the same as `solx job list`."""
    called: list[str] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_list", lambda **kw: called.append("list") or 0)
    assert invoke(["jobs", "list"]) == 0
    assert called == ["list"]


def test_ls_alias(monkeypatch) -> None:
    called: list[str] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_list", lambda **kw: called.append("list") or 0)
    assert invoke(["job", "ls"]) == 0
    assert called == ["list"]


def test_jobs_alias_after_root_json(monkeypatch) -> None:
    """The alias rewrite also applies after root options: `--json jobs list`."""
    called: list[str] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_list", lambda **kw: called.append("list") or 0)
    assert invoke(["--json", "jobs", "list"]) == 0
    assert called == ["list"]


def test_top_level_jump_routes_to_job_jump(monkeypatch) -> None:
    """`solx jump` should run the same body as `solx job jump`."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_jump", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["jump", "12345", "--quiet"]) == 0
    assert captured[0]["jobid_arg"] == "12345"
    assert captured[0]["quiet"] is True


# ---- global output flags ------------------------------------------------


def test_global_json_forces_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())
    # global --json before the subcommand; config show has no local --json here
    assert invoke(["--json", "config", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["default_shell"] == "zsh"


# ---- job subcommands ----------------------------------------------------


def test_job_start_passthrough(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "default", "--", "--mem=128G"]) == 0
    assert captured[0]["template_name"] == "default"
    assert captured[0]["passthrough"] == ["--mem=128G"]


def test_job_start_dry_run_flag(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "--dry-run"]) == 0
    assert captured[0]["dry_run"] is True


def test_job_start_timeout_override(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "--timeout", "5m"]) == 0
    assert captured[0]["timeout_override"] == 300


def test_job_start_timeout_equals_form(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "--timeout=5m", "-n"]) == 0
    assert captured[0]["timeout_override"] == 300
    assert captured[0]["dry_run"] is True


def test_job_start_invalid_timeout(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())
    assert invoke(["job", "start", "--timeout", "never"]) == 2


def test_job_start_template_after_double_dash(monkeypatch) -> None:
    """The first token not consumed by a known option names the template,
    including tokens after `--`."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "-n", "--", "--mem=128G"]) == 0
    assert captured[0]["template_name"] == "--mem=128G"
    assert captured[0]["passthrough"] == []
    assert captured[0]["dry_run"] is True


def test_job_start_mixed_passthrough_order(monkeypatch) -> None:
    """Known options are consumed wherever they appear; everything else is
    passthrough in its original order."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "gpu", "-n", "--mem=128G", "-c", "8"]) == 0
    assert captured[0]["template_name"] == "gpu"
    assert captured[0]["passthrough"] == ["--mem=128G", "-c", "8"]
    assert captured[0]["dry_run"] is True


def test_job_start_trailing_json_is_passthrough(monkeypatch) -> None:
    """A --json after `job start` belongs to the salloc passthrough."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["job", "start", "gpu", "--json"]) == 0
    assert captured[0]["template_name"] == "gpu"
    assert captured[0]["passthrough"] == ["--json"]


def test_root_json_before_job_start(monkeypatch) -> None:
    """The root --json still applies on the `job start` path."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_start", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["--json", "job", "start", "-n"]) == 0
    assert captured[0]["dry_run"] is True
    assert captured[0]["out"].json_mode is True


def test_job_stop_yes_flag(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_stop", lambda **kw: captured.append(kw) or 0)
    assert invoke(["job", "stop", "12345", "-y"]) == 0
    assert captured[0]["yes"] is True
    assert captured[0]["dry_run"] is False


def test_job_stop_force_is_alias_for_yes(monkeypatch) -> None:
    """`-f`/`--force` is interchangeable with `-y`/`--yes` for skipping the prompt."""
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_stop", lambda **kw: captured.append(kw) or 0)
    assert invoke(["job", "stop", "12345", "--force"]) == 0
    assert captured[0]["yes"] is True


def test_job_time_no_arg(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_time", lambda **kw: captured.append(kw) or 0)
    assert invoke(["job", "time"]) == 0
    assert captured[0]["jobid_arg"] is None


# ---- keep ---------------------------------------------------------------


def test_keep_dry_run(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import keep as keep_mod

    monkeypatch.setattr(keep_mod, "cmd_keep", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert invoke(["keep", "-n"]) == 0
    assert captured[0]["dry_run"] is True


def test_keep_invalid_stage(capsys) -> None:
    assert invoke(["keep", "--stage", "bogus"]) == 2
    captured = capsys.readouterr()
    assert "invalid --stage" in captured.err or "invalid --stage" in captured.out


def test_keep_solkeep_flag_and_missing_config(monkeypatch, tmp_path) -> None:
    """`solx keep --solkeep ...` works with no config.toml (config passed as None)."""
    captured: list[dict] = []
    from solx import keep as keep_mod

    monkeypatch.setattr(keep_mod, "cmd_keep", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "absent.toml")
    assert invoke(["keep", "--solkeep", "/tmp/mk", "-y"]) == 0
    assert str(captured[0]["solkeep"]) == "/tmp/mk"
    assert captured[0]["config"] is None  # missing config tolerated for keep


def test_keep_full_flag_set(monkeypatch, tmp_path) -> None:
    captured: list[dict] = []
    from solx import keep as keep_mod

    monkeypatch.setattr(keep_mod, "cmd_keep", lambda **kw: captured.append(kw) or 0)
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())

    assert (
        invoke(
            [
                "keep",
                "--stage", "pending",
                "--csv-dir", str(tmp_path),
                "-j", "4",
                "-y",
                "-v",
            ]
        )
        == 0
    )
    kw = captured[0]
    assert kw["stage"] == "pending"
    assert kw["csv_dir"] == tmp_path
    assert kw["jobs_n"] == 4
    assert kw["yes"] is True
    assert kw["verbose"] is True


# ---- init ---------------------------------------------------------------


def test_init_default(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(init_mod, "cmd_init", lambda **kw: captured.append(kw) or 0)
    assert invoke(["init"]) == 0
    assert captured[0]["force"] is False


def test_init_force(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(init_mod, "cmd_init", lambda **kw: captured.append(kw) or 0)
    assert invoke(["init", "-f"]) == 0
    assert captured[0]["force"] is True


def test_init_yes_is_alias_for_force(monkeypatch) -> None:
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(init_mod, "cmd_init", lambda **kw: captured.append(kw) or 0)
    assert invoke(["init", "-y"]) == 0
    assert captured[0]["force"] is True


# ---- config -------------------------------------------------------------


def test_config_show(monkeypatch, capsys) -> None:
    config = Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={
            "default": JobTemplate(
                name="default", partition="lightwork", time="1-0", qos="public"
            )
        },
    )
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: config)
    assert invoke(["config", "show"]) == 0
    assert "lightwork" in capsys.readouterr().out


def test_config_show_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_mod, "_load_or_exit", lambda *a, **kw: fake_config())
    assert invoke(["config", "show", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["default_shell"] == "zsh"
    assert "default" in data["templates"]


def test_config_edit_no_config(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "absent.toml")
    assert invoke(["config", "edit"]) == 2
    assert "no config at" in capsys.readouterr().err


def test_config_edit_splits_editor_flags(monkeypatch, tmp_path) -> None:
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
    assert invoke(["config", "edit"]) == 0
    assert captured["argv"] == ["myed", "--wait", str(cfgfile)]


def test_config_import_solkeep_wiring(monkeypatch) -> None:
    """`solx config import-solkeep --solkeep F` routes to init.cmd_import_solkeep."""
    captured: list[dict] = []
    from solx import init as init_mod

    monkeypatch.setattr(
        init_mod, "cmd_import_solkeep", lambda **kw: captured.append(kw) or 0
    )
    assert invoke(["config", "import-solkeep", "--solkeep", "/tmp/mk", "-f"]) == 0
    assert str(captured[0]["solkeep"]) == "/tmp/mk"
    assert captured[0]["force"] is True


# ---- completions --------------------------------------------------------


def test_completions_invalid_shell(capsys) -> None:
    assert invoke(["completions", "tcsh"]) == 2
    assert "unknown shell 'tcsh'" in capsys.readouterr().err


def test_completions_bash_emits_script(capsys) -> None:
    assert invoke(["completions", "bash"]) == 0
    out = capsys.readouterr().out
    assert "complete -F _solx solx" in out


# ---- import hygiene -------------------------------------------------------


def test_dispatch_never_imports_typer(monkeypatch) -> None:
    from solx import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "cmd_list", lambda **kw: 0)
    assert invoke(["job", "list"]) == 0
    assert "typer" not in sys.modules


def test_importing_main_is_lean() -> None:
    """`import solx.main` must not pull in rich (or any CLI framework)."""
    code = (
        "import solx.main, sys; "
        "assert 'rich' not in sys.modules; "
        "assert 'typer' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_python_m_solx_version() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "solx", "--version"], capture_output=True, text=True
    )
    assert res.returncode == 0
    assert res.stdout.strip() == __version__

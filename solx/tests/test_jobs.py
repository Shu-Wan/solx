from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from solx import jobs as jobs_mod
from solx import slurm
from solx.config import Config, JobTemplate


# ---- helpers -------------------------------------------------------------


def silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False, width=200)


def make_runner(*, code: int = 0, stdout: str = "", stderr: str = ""):
    captured: list[list[str]] = []

    def runner(argv):
        captured.append(list(argv))
        return code, stdout, stderr

    return runner, captured


def basic_config() -> Config:
    t_default = JobTemplate(
        name="default", partition="lightwork", time="1-0", qos="public"
    )
    t_debug = JobTemplate(name="debug", partition="htc", time="0-1")
    return Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={"default": t_default, "debug": t_debug},
    )


# ---- list ----------------------------------------------------------------


def test_list_empty(monkeypatch) -> None:
    runner, _ = make_runner(stdout="")
    code = jobs_mod.cmd_list(runner=runner, console=silent_console())
    assert code == 0


def test_list_renders_jobs() -> None:
    out = (
        "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n"
        "67890|notebook|PENDING|00:00:00|02:00:00|htc|(Resources)\n"
    )
    runner, _ = make_runner(stdout=out)
    code = jobs_mod.cmd_list(runner=runner, console=silent_console())
    assert code == 0


def test_list_propagates_squeue_failure() -> None:
    runner, _ = make_runner(code=1, stderr="slurmctld is down")
    code = jobs_mod.cmd_list(runner=runner, console=silent_console())
    assert code == 1


# ---- start ---------------------------------------------------------------


def test_start_dry_run_prints_argv() -> None:
    cfg = basic_config()
    code = jobs_mod.cmd_start(
        config=cfg,
        template_name="debug",
        dry_run=True,
        timeout_override=None,
        passthrough=[],
        console=silent_console(),
    )
    assert code == 0


def test_start_uses_default_template_when_none() -> None:
    cfg = basic_config()
    captured: dict = {}

    def fake_runner(argv):
        captured["argv"] = argv
        return 0, "", "salloc: Granted job allocation 99999\n"

    code = jobs_mod.cmd_start(
        config=cfg,
        template_name=None,
        dry_run=False,
        timeout_override=None,
        passthrough=[],
        salloc_runner=fake_runner,
        console=silent_console(),
    )
    assert code == 0
    assert "-J" in captured["argv"]
    assert "solx-default" in captured["argv"]


def test_start_unknown_template() -> None:
    cfg = basic_config()
    code = jobs_mod.cmd_start(
        config=cfg,
        template_name="nope",
        dry_run=True,
        timeout_override=None,
        passthrough=[],
        console=silent_console(),
    )
    assert code == 1


def test_start_passthrough_appended() -> None:
    cfg = basic_config()
    captured: dict = {}

    def fake_runner(argv):
        captured["argv"] = argv
        return 0, "", "salloc: Granted job allocation 11111\n"

    jobs_mod.cmd_start(
        config=cfg,
        template_name="debug",
        dry_run=False,
        timeout_override=None,
        passthrough=["--mem=128G"],
        salloc_runner=fake_runner,
        console=silent_console(),
    )
    assert "--mem=128G" in captured["argv"]
    # passthrough comes after template's flags
    assert captured["argv"][-1] == "--mem=128G"


def test_start_salloc_failure() -> None:
    cfg = basic_config()

    def fake_runner(argv):
        return 1, "", "salloc: error: invalid partition\n"

    code = jobs_mod.cmd_start(
        config=cfg,
        template_name="debug",
        dry_run=False,
        timeout_override=None,
        passthrough=[],
        salloc_runner=fake_runner,
        console=silent_console(),
    )
    assert code == 1


# ---- stop ----------------------------------------------------------------


def test_stop_yes_and_dry_run_mutually_exclusive() -> None:
    runner, _ = make_runner()
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=True, dry_run=True,
        runner=runner, console=silent_console(),
    )
    assert code == 2


def test_stop_dry_run() -> None:
    runner, captured = make_runner()
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=False, dry_run=True,
        runner=runner, console=silent_console(),
    )
    assert code == 0
    # dry-run does NOT execute
    assert captured == []


def test_stop_with_yes_skips_prompt() -> None:
    runner, captured = make_runner()
    confirms: list = []
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=True, dry_run=False,
        runner=runner, console=silent_console(),
        confirm_fn=lambda *a, **kw: confirms.append(True) or True,
    )
    assert code == 0
    assert confirms == []  # never called
    assert captured == [["scancel", "12345"]]


def test_stop_prompts_and_proceeds() -> None:
    runner, captured = make_runner()
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=False, dry_run=False,
        runner=runner, console=silent_console(),
        confirm_fn=lambda *a, **kw: True,
    )
    assert code == 0
    assert captured == [["scancel", "12345"]]


def test_stop_prompts_and_aborts() -> None:
    runner, captured = make_runner()
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=False, dry_run=False,
        runner=runner, console=silent_console(),
        confirm_fn=lambda *a, **kw: False,
    )
    assert code == 1
    assert captured == []  # not executed


def test_stop_ambiguous_jobs() -> None:
    out = (
        "12345|a|RUNNING|00:01|00:59|p|n\n"
        "67890|b|RUNNING|00:01|00:59|p|n\n"
    )
    runner, _ = make_runner(stdout=out)
    code = jobs_mod.cmd_stop(
        jobid_arg=None, yes=True, dry_run=False,
        runner=runner, console=silent_console(),
    )
    assert code == 2


# ---- jump ----------------------------------------------------------------


def test_jump_builds_correct_argv() -> None:
    cfg = basic_config()
    runner, _ = make_runner()
    captured: list = []
    code = jobs_mod.cmd_jump(
        config=cfg,
        jobid_arg="12345",
        runner=runner,
        exec_fn=lambda argv: captured.append(argv),
        console=silent_console(),
    )
    assert code == 0
    assert captured == [["srun", "--jobid=12345", "--pty", "zsh"]]


def test_jump_uses_env_jobid_on_compute_node(monkeypatch) -> None:
    cfg = basic_config()
    runner, _ = make_runner()
    captured: list = []
    monkeypatch.setenv("SLURM_JOB_ID", "99999")
    jobs_mod.cmd_jump(
        config=cfg,
        jobid_arg=None,
        runner=runner,
        exec_fn=lambda argv: captured.append(argv),
        console=silent_console(),
    )
    assert captured == [["srun", "--jobid=99999", "--pty", "zsh"]]


# ---- time ----------------------------------------------------------------


def test_time_prints_left() -> None:
    runner, _ = make_runner(stdout="00:42:13\n")
    code = jobs_mod.cmd_time(
        jobid_arg="12345",
        runner=runner,
        console=silent_console(),
    )
    assert code == 0


def test_time_squeue_error() -> None:
    runner, _ = make_runner(code=1, stderr="invalid jobid")
    code = jobs_mod.cmd_time(
        jobid_arg="12345", runner=runner, console=silent_console(),
    )
    assert code == 1

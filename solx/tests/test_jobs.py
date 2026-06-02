from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console

from solx import jobs as jobs_mod
from solx import slurm
from solx.config import Config, JobTemplate
from solx.output import Out


# ---- helpers -------------------------------------------------------------


def make_out(*, json_mode: bool = False, interactive: bool = True) -> Out:
    so = Console(file=StringIO(), force_terminal=False, width=200)
    se = Console(file=StringIO(), force_terminal=False, width=200)
    return Out(json_mode=json_mode, interactive=interactive, stdout=so, stderr=se)


def make_runner(*, code: int = 0, stdout: str = "", stderr: str = ""):
    captured: list[list[str]] = []

    def runner(argv):
        captured.append(list(argv))
        return code, stdout, stderr

    return runner, captured


def routed_runner(*, jobs_out: str = "", time_out: str = "00:10:00\n", scancel_code: int = 0):
    """A runner that returns different output per Slurm subcommand.

    Needed when one command makes several calls (e.g. `time` does squeue -u for
    resolution, then squeue -O TimeLeft).
    """
    captured: list[list[str]] = []

    def runner(argv):
        captured.append(list(argv))
        if "-O" in argv:  # squeue ... -O TimeLeft
            return 0, time_out, ""
        if argv[:1] == ["squeue"]:  # squeue -u $USER
            return 0, jobs_out, ""
        if argv[:1] == ["scancel"]:
            return scancel_code, "", ("scancel error" if scancel_code else "")
        return 0, "", ""

    return runner, captured


def basic_config() -> Config:
    return Config(
        default_shell="zsh",
        default_template="default",
        start_timeout_seconds=600,
        templates={
            "default": JobTemplate(name="default", partition="lightwork", time="1-0", qos="public"),
            "debug": JobTemplate(name="debug", partition="htc", time="0-1"),
        },
    )


TWO_RUNNING = (
    "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n"
    "67890|notebook|RUNNING|00:01:00|00:59:00|htc|sg010\n"
)


# ---- list ----------------------------------------------------------------


def test_list_empty() -> None:
    runner, _ = make_runner(stdout="")
    assert jobs_mod.cmd_list(runner=runner, out=make_out()) == 0


def test_list_renders_jobs() -> None:
    runner, _ = make_runner(stdout=TWO_RUNNING)
    out = make_out()
    assert jobs_mod.cmd_list(runner=runner, out=out) == 0
    assert "67890" in out.stdout.file.getvalue()


def test_list_json() -> None:
    runner, _ = make_runner(stdout=TWO_RUNNING)
    out = make_out(json_mode=True)
    assert jobs_mod.cmd_list(runner=runner, out=out) == 0
    data = json.loads(out.stdout.file.getvalue())
    assert [j["job_id"] for j in data] == ["12345", "67890"]
    assert data[0]["state"] == "RUNNING"


def test_list_propagates_squeue_failure() -> None:
    runner, _ = make_runner(code=1, stderr="slurmctld is down")
    assert jobs_mod.cmd_list(runner=runner, out=make_out()) == 1


# ---- start ---------------------------------------------------------------


def test_start_dry_run_prints_argv() -> None:
    out = make_out()
    code = jobs_mod.cmd_start(
        config=basic_config(), template_name="debug", dry_run=True,
        timeout_override=None, passthrough=[], out=out,
    )
    assert code == 0
    assert "salloc" in out.stdout.file.getvalue()


def test_start_dry_run_json() -> None:
    out = make_out(json_mode=True)
    jobs_mod.cmd_start(
        config=basic_config(), template_name="debug", dry_run=True,
        timeout_override=None, passthrough=[], out=out,
    )
    data = json.loads(out.stdout.file.getvalue())
    assert data["dry_run"] is True
    assert data["argv"][0] == "salloc"
    assert data["template"] == "debug"


def test_start_uses_default_template_when_none() -> None:
    captured: dict = {}

    def fake_runner(argv):
        captured["argv"] = argv
        return 0, "", "salloc: Granted job allocation 99999\n"

    code = jobs_mod.cmd_start(
        config=basic_config(), template_name=None, dry_run=False,
        timeout_override=None, passthrough=[], salloc_runner=fake_runner, out=make_out(),
    )
    assert code == 0
    assert "-J" in captured["argv"] and "solx-default" in captured["argv"]


def test_start_json_emits_jobid() -> None:
    def fake_runner(argv):
        return 0, "", "salloc: Granted job allocation 99999\n"

    out = make_out(json_mode=True)
    jobs_mod.cmd_start(
        config=basic_config(), template_name="debug", dry_run=False,
        timeout_override=None, passthrough=[], salloc_runner=fake_runner, out=out,
    )
    assert json.loads(out.stdout.file.getvalue()) == {"jobid": "99999", "template": "debug"}


def test_start_unknown_template() -> None:
    code = jobs_mod.cmd_start(
        config=basic_config(), template_name="nope", dry_run=True,
        timeout_override=None, passthrough=[], out=make_out(),
    )
    assert code == 1


def test_start_passthrough_appended() -> None:
    captured: dict = {}

    def fake_runner(argv):
        captured["argv"] = argv
        return 0, "", "salloc: Granted job allocation 11111\n"

    jobs_mod.cmd_start(
        config=basic_config(), template_name="debug", dry_run=False,
        timeout_override=None, passthrough=["--mem=128G"], salloc_runner=fake_runner, out=make_out(),
    )
    assert captured["argv"][-1] == "--mem=128G"


def test_start_salloc_failure() -> None:
    def fake_runner(argv):
        return 1, "", "salloc: error: invalid partition\n"

    code = jobs_mod.cmd_start(
        config=basic_config(), template_name="debug", dry_run=False,
        timeout_override=None, passthrough=[], salloc_runner=fake_runner, out=make_out(),
    )
    assert code == 1


# ---- stop ----------------------------------------------------------------


def test_stop_yes_and_dry_run_mutually_exclusive() -> None:
    runner, _ = make_runner()
    code = jobs_mod.cmd_stop(jobid_arg="12345", yes=True, dry_run=True, runner=runner, out=make_out())
    assert code == 2


def test_stop_dry_run() -> None:
    runner, captured = make_runner()
    code = jobs_mod.cmd_stop(jobid_arg="12345", yes=False, dry_run=True, runner=runner, out=make_out())
    assert code == 0
    assert captured == []


def test_stop_with_yes_skips_prompt() -> None:
    runner, captured = make_runner()
    confirms: list = []
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=True, dry_run=False, runner=runner, out=make_out(),
        confirm_fn=lambda *a, **kw: confirms.append(True) or True,
    )
    assert code == 0
    assert confirms == []
    assert captured == [["scancel", "12345"]]


def test_stop_prompts_and_proceeds() -> None:
    runner, captured = make_runner()
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=False, dry_run=False, runner=runner,
        out=make_out(interactive=True), confirm_fn=lambda *a, **kw: True,
    )
    assert code == 0
    assert captured == [["scancel", "12345"]]


def test_stop_prompts_and_aborts() -> None:
    runner, captured = make_runner()
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=False, dry_run=False, runner=runner,
        out=make_out(interactive=True), confirm_fn=lambda *a, **kw: False,
    )
    assert code == 1
    assert captured == []


def test_stop_non_interactive_refuses() -> None:
    """No TTY on stdin + no -y/-n -> refuse, exit 2, never prompt or cancel."""
    runner, captured = make_runner()
    out = make_out(interactive=False)
    code = jobs_mod.cmd_stop(
        jobid_arg="12345", yes=False, dry_run=False, runner=runner, out=out,
        confirm_fn=lambda *a, **kw: pytest.fail("must not prompt"),
    )
    assert code == 2
    assert captured == []
    assert "non-interactive" in out.stderr.file.getvalue()


def test_stop_ambiguous_jobs_no_autopick() -> None:
    runner, captured = routed_runner(jobs_out=TWO_RUNNING)
    out = make_out()
    code = jobs_mod.cmd_stop(jobid_arg=None, yes=True, dry_run=False, runner=runner, out=out)
    assert code == 2
    # never cancelled anything
    assert not any(a[:1] == ["scancel"] for a in captured)
    assert "multiple jobs" in out.stderr.file.getvalue()


def test_stop_ambiguous_json_lists_candidates() -> None:
    runner, _ = routed_runner(jobs_out=TWO_RUNNING)
    out = make_out(json_mode=True)
    code = jobs_mod.cmd_stop(jobid_arg=None, yes=True, dry_run=False, runner=runner, out=out)
    assert code == 2
    data = json.loads(out.stdout.file.getvalue())
    assert {j["job_id"] for j in data["jobs"]} == {"12345", "67890"}


def test_stop_self_cancel_warns(monkeypatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "55555")
    runner, captured = routed_runner()
    out = make_out()
    code = jobs_mod.cmd_stop(jobid_arg=None, yes=True, dry_run=False, runner=runner, out=out)
    assert code == 0
    assert ["scancel", "55555"] in captured
    err = out.stderr.file.getvalue()
    assert "55555" in err and "allocation you're inside" in err


def test_stop_self_cancel_warns_in_dry_run(monkeypatch) -> None:
    """Dry-run preview must still surface that the target is the current session."""
    monkeypatch.setenv("SLURM_JOB_ID", "55555")
    runner, captured = routed_runner()
    out = make_out(json_mode=True)
    code = jobs_mod.cmd_stop(jobid_arg=None, yes=False, dry_run=True, runner=runner, out=out)
    assert code == 0
    assert not any(a[:1] == ["scancel"] for a in captured)  # nothing cancelled
    assert "allocation you're inside" in out.stderr.file.getvalue()
    assert json.loads(out.stdout.file.getvalue())["inside_allocation"] is True


def test_stop_json_cancelled() -> None:
    runner, _ = make_runner()
    out = make_out(json_mode=True)
    jobs_mod.cmd_stop(jobid_arg="12345", yes=True, dry_run=False, runner=runner, out=out)
    assert json.loads(out.stdout.file.getvalue()) == {"cancelled": "12345"}


# ---- jump ----------------------------------------------------------------


def test_jump_builds_correct_argv() -> None:
    runner, _ = make_runner()
    captured: list = []
    code = jobs_mod.cmd_jump(
        config=basic_config(), jobid_arg="12345", runner=runner,
        exec_fn=lambda argv: captured.append(argv), out=make_out(),
    )
    assert code == 0
    assert captured == [["srun", "--jobid=12345", "--overlap", "--pty", "zsh"]]


def test_jump_from_inside_warns_and_proceeds(monkeypatch) -> None:
    """Attach is non-destructive: warn about nesting but still attach (exit 0)."""
    monkeypatch.setenv("SLURM_JOB_ID", "99999")
    runner, _ = make_runner()
    captured: list = []
    out = make_out()
    code = jobs_mod.cmd_jump(
        config=basic_config(), jobid_arg=None, runner=runner,
        exec_fn=lambda argv: captured.append(argv), out=out,
    )
    assert code == 0
    assert captured == [["srun", "--jobid=99999", "--overlap", "--pty", "zsh"]]
    assert "already inside job 99999" in out.stderr.file.getvalue()


def test_jump_inside_quiet_suppresses_warning(monkeypatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "99999")
    runner, _ = make_runner()
    captured: list = []
    out = make_out()
    code = jobs_mod.cmd_jump(
        config=basic_config(), jobid_arg=None, quiet=True, runner=runner,
        exec_fn=lambda argv: captured.append(argv), out=out,
    )
    assert code == 0
    assert captured == [["srun", "--jobid=99999", "--overlap", "--pty", "zsh"]]
    assert out.stderr.file.getvalue() == ""


def test_jump_most_recent_running() -> None:
    runner, _ = routed_runner(jobs_out=TWO_RUNNING)
    captured: list = []
    out = make_out()
    code = jobs_mod.cmd_jump(
        config=basic_config(), jobid_arg=None, runner=runner,
        exec_fn=lambda argv: captured.append(argv), out=out,
    )
    assert code == 0
    # highest jobid (67890) is "most recent"
    assert captured == [["srun", "--jobid=67890", "--overlap", "--pty", "zsh"]]
    assert "most recent" in out.stderr.file.getvalue()


def test_jump_no_running_job() -> None:
    pending = "12345|nb|PENDING|00:00|01:00|htc|(Resources)\n"
    runner, _ = routed_runner(jobs_out=pending)
    captured: list = []
    out = make_out()
    code = jobs_mod.cmd_jump(
        config=basic_config(), jobid_arg=None, runner=runner,
        exec_fn=lambda argv: captured.append(argv), out=out,
    )
    assert code == 1
    assert captured == []
    assert "no running job" in out.stderr.file.getvalue()


# ---- time ----------------------------------------------------------------


def test_time_prints_left() -> None:
    runner, _ = make_runner(stdout="00:42:13\n")
    out = make_out()
    code = jobs_mod.cmd_time(jobid_arg="12345", runner=runner, out=out)
    assert code == 0
    assert "00:42:13" in out.stdout.file.getvalue()


def test_time_json() -> None:
    runner, _ = make_runner(stdout="00:42:13\n")
    out = make_out(json_mode=True)
    jobs_mod.cmd_time(jobid_arg="12345", runner=runner, out=out)
    assert json.loads(out.stdout.file.getvalue()) == {"jobid": "12345", "time_left": "00:42:13"}


def test_time_most_recent() -> None:
    runner, _ = routed_runner(jobs_out=TWO_RUNNING, time_out="01:00:00\n")
    out = make_out()
    code = jobs_mod.cmd_time(jobid_arg=None, runner=runner, out=out)
    assert code == 0
    data_line = out.stdout.file.getvalue()
    assert "01:00:00" in data_line
    assert "most recent 67890" in out.stderr.file.getvalue()


def test_time_squeue_error() -> None:
    runner, _ = make_runner(code=1, stderr="invalid jobid")
    assert jobs_mod.cmd_time(jobid_arg="12345", runner=runner, out=make_out()) == 1

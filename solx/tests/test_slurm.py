from __future__ import annotations

import pytest

from solx.config import JobTemplate
from solx import slurm
from solx.slurm import Job, SlurmError


# ---- runner helper -------------------------------------------------------


def make_runner(*, code: int = 0, stdout: str = "", stderr: str = ""):
    captured: dict = {}

    def runner(argv):
        captured["argv"] = argv
        return code, stdout, stderr

    return runner, captured


# ---- squeue --------------------------------------------------------------


def test_squeue_user_jobs_parses_rows() -> None:
    out = (
        "12345|solx-default|RUNNING|00:05:23|00:54:37|lightwork|sg045\n"
        "12346|notebook|PENDING|00:00:00|01:00:00|htc|(Resources)\n"
    )
    runner, cap = make_runner(stdout=out)
    jobs = slurm.squeue_user_jobs(user="sparky", runner=runner)
    assert len(jobs) == 2
    assert jobs[0] == Job(
        job_id="12345",
        name="solx-default",
        state="RUNNING",
        time_used="00:05:23",
        time_left="00:54:37",
        partition="lightwork",
        node_list="sg045",
    )
    assert "-u" in cap["argv"] and "sparky" in cap["argv"]


def test_squeue_user_jobs_empty() -> None:
    runner, _ = make_runner(stdout="")
    assert slurm.squeue_user_jobs(user="sparky", runner=runner) == []


def test_squeue_user_jobs_failure() -> None:
    runner, _ = make_runner(code=1, stderr="slurmctld is down")
    with pytest.raises(SlurmError, match="slurmctld is down"):
        slurm.squeue_user_jobs(user="sparky", runner=runner)


# ---- resolve_jobid -------------------------------------------------------


def test_resolve_jobid_arg_wins() -> None:
    runner, cap = make_runner()
    jid, jobs = slurm.resolve_jobid("99999", env={"SLURM_JOB_ID": "11111"}, runner=runner)
    assert jid == "99999"
    assert jobs is None
    assert "argv" not in cap  # never queried squeue


def test_resolve_jobid_uses_env_on_compute_node() -> None:
    runner, cap = make_runner()
    jid, jobs = slurm.resolve_jobid(
        None, env={"SLURM_JOB_ID": "55555"}, runner=runner
    )
    assert jid == "55555"
    assert jobs is None
    assert "argv" not in cap


def test_resolve_jobid_single_running_job() -> None:
    out = "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n"
    runner, _ = make_runner(stdout=out)
    jid, jobs = slurm.resolve_jobid(None, env={}, user="sparky", runner=runner)
    assert jid == "12345"
    assert jobs is None


def test_resolve_jobid_zero_jobs() -> None:
    runner, _ = make_runner(stdout="")
    with pytest.raises(SlurmError, match="no jobs found"):
        slurm.resolve_jobid(None, env={}, user="sparky", runner=runner)


def test_resolve_jobid_ambiguous_returns_table() -> None:
    out = (
        "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n"
        "67890|notebook|RUNNING|00:01:00|00:59:00|htc|sg010\n"
    )
    runner, _ = make_runner(stdout=out)
    jid, jobs = slurm.resolve_jobid(None, env={}, user="sparky", runner=runner)
    assert jid == ""
    assert jobs is not None
    assert {j.job_id for j in jobs} == {"12345", "67890"}


# ---- argv builders -------------------------------------------------------


def test_salloc_argv_minimal() -> None:
    t = JobTemplate(name="default", partition="lightwork", time="1-0")
    argv = slurm.salloc_argv(t)
    assert argv == [
        "salloc",
        "--no-shell",
        "-J",
        "solx-default",
        "-p",
        "lightwork",
        "-t",
        "1-0",
    ]


def test_salloc_argv_full() -> None:
    t = JobTemplate(
        name="gpu",
        partition="public",
        time="0-4",
        qos="public",
        gres="gpu:a100:1",
        extra_args=("--mem=64G", "--cpus-per-task=8"),
    )
    argv = slurm.salloc_argv(t, passthrough=["--mail-type=END"])
    assert argv == [
        "salloc", "--no-shell", "-J", "solx-gpu",
        "-p", "public",
        "-t", "0-4",
        "-q", "public",
        "--gres=gpu:a100:1",
        "--mem=64G", "--cpus-per-task=8",
        "--mail-type=END",
    ]


def test_scancel_argv() -> None:
    assert slurm.scancel_argv("12345") == ["scancel", "12345"]


def test_srun_pty_argv() -> None:
    assert slurm.srun_pty_argv("12345", "zsh") == [
        "srun",
        "--jobid=12345",
        "--pty",
        "zsh",
    ]


def test_squeue_time_left_argv() -> None:
    argv = slurm.squeue_time_left_argv("12345")
    assert argv == ["squeue", "-h", "-j", "12345", "-O", "TimeLeft"]


# ---- salloc parse + run --------------------------------------------------


def test_parse_granted_jobid() -> None:
    text = (
        "salloc: Pending job allocation 51642835\n"
        "salloc: job 51642835 queued and waiting for resources\n"
        "salloc: job 51642835 has been allocated resources\n"
        "salloc: Granted job allocation 51642835\n"
    )
    assert slurm.parse_granted_jobid(text) == "51642835"


def test_parse_granted_jobid_missing() -> None:
    with pytest.raises(SlurmError, match="could not parse"):
        slurm.parse_granted_jobid("salloc: error: queue down\n")


def test_run_salloc_success_via_runner() -> None:
    runner, cap = make_runner(
        stderr="salloc: Granted job allocation 99999\n",
    )
    argv = ["salloc", "--no-shell"]
    jid = slurm.run_salloc(argv, timeout_seconds=60, runner=runner)
    assert jid == "99999"
    assert cap["argv"] == argv


def test_run_salloc_failure_via_runner() -> None:
    runner, _ = make_runner(code=1, stderr="salloc: error: invalid partition\n")
    with pytest.raises(SlurmError, match="invalid partition"):
        slurm.run_salloc(["salloc"], timeout_seconds=60, runner=runner)

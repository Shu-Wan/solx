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


TWO_RUNNING = (
    "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n"
    "67890|notebook|RUNNING|00:01:00|00:59:00|htc|sg010\n"
)


def test_resolve_jobid_arg_wins() -> None:
    runner, cap = make_runner()
    res = slurm.resolve_jobid(
        "99999", verb=slurm.VERB_STOP, env={"SLURM_JOB_ID": "11111"}, runner=runner
    )
    assert res.job_id == "99999"
    assert res.source == "arg"
    assert res.inside is True and res.inside_job_id == "11111"
    assert "argv" not in cap  # never queried squeue


def test_resolve_jobid_uses_env_on_compute_node() -> None:
    runner, cap = make_runner()
    res = slurm.resolve_jobid(None, verb=slurm.VERB_TIME, env={"SLURM_JOB_ID": "55555"}, runner=runner)
    assert res.job_id == "55555"
    assert res.source == "inside"
    assert res.acting_on_current is True
    assert "argv" not in cap


def test_resolve_jobid_single_running_job() -> None:
    out = "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n"
    runner, _ = make_runner(stdout=out)
    res = slurm.resolve_jobid(None, verb=slurm.VERB_STOP, env={}, user="sparky", runner=runner)
    assert res.job_id == "12345"
    assert res.source == "single"
    assert res.ambiguous is False


def test_resolve_jobid_zero_jobs() -> None:
    runner, _ = make_runner(stdout="")
    res = slurm.resolve_jobid(None, verb=slurm.VERB_TIME, env={}, user="sparky", runner=runner)
    assert res.job_id is None
    assert res.error and "no jobs found" in res.error


def test_resolve_jobid_stop_ambiguous_no_autopick() -> None:
    runner, _ = make_runner(stdout=TWO_RUNNING)
    res = slurm.resolve_jobid(None, verb=slurm.VERB_STOP, env={}, user="sparky", runner=runner)
    assert res.job_id is None
    assert res.ambiguous is True
    assert {j.job_id for j in res.candidates} == {"12345", "67890"}


def test_resolve_jobid_time_picks_most_recent() -> None:
    runner, _ = make_runner(stdout=TWO_RUNNING)
    res = slurm.resolve_jobid(None, verb=slurm.VERB_TIME, env={}, user="sparky", runner=runner)
    assert res.job_id == "67890"  # highest jobid == most recent
    assert res.source == "most-recent"
    assert res.ambiguous is False


def test_resolve_jobid_jump_filters_running_only() -> None:
    out = (
        "12345|a|RUNNING|00:01|00:59|p|sg045\n"
        "67890|b|PENDING|00:00|01:00|p|(Resources)\n"
    )
    runner, _ = make_runner(stdout=out)
    res = slurm.resolve_jobid(None, verb=slurm.VERB_JUMP, env={}, user="sparky", runner=runner)
    # only the RUNNING job is an attach candidate -> unambiguous
    assert res.job_id == "12345"
    assert res.source == "single"


def test_resolve_jobid_jump_no_running() -> None:
    out = "67890|b|PENDING|00:00|01:00|p|(Resources)\n"
    runner, _ = make_runner(stdout=out)
    res = slurm.resolve_jobid(None, verb=slurm.VERB_JUMP, env={}, user="sparky", runner=runner)
    assert res.job_id is None
    assert res.error and "no running job" in res.error


def test_most_recent_highest_jobid() -> None:
    jobs = [
        Job("100", "a", "RUNNING", "", "", "p"),
        Job("9999", "b", "RUNNING", "", "", "p"),
        Job("250", "c", "RUNNING", "", "", "p"),
    ]
    assert slurm.most_recent(jobs).job_id == "9999"


def test_most_recent_array_ids() -> None:
    jobs = [Job("100_1", "a", "R", "", "", "p"), Job("100_7", "b", "R", "", "", "p")]
    assert slurm.most_recent(jobs).job_id == "100_7"


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
    # --overlap lets the step share the allocation's busy resources.
    assert slurm.srun_pty_argv("12345", "zsh") == [
        "srun",
        "--jobid=12345",
        "--overlap",
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

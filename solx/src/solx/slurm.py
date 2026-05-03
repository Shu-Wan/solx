"""Thin wrappers around `squeue`, `scancel`, `salloc`, and `srun`.

We don't try to be a Slurm client library — every function shells out and
parses the result. Tests inject `runner` so they can mock subprocess
without monkey-patching globals.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable

from solx.config import JobTemplate


# --- types -----------------------------------------------------------------

# A Runner takes argv and returns (returncode, stdout, stderr).
Runner = Callable[[list[str]], tuple[int, str, str]]


@dataclass(frozen=True)
class Job:
    """One row of `squeue -u $USER`."""

    job_id: str
    name: str
    state: str
    time_used: str
    time_left: str
    partition: str
    node_list: str = ""

    @classmethod
    def from_squeue_row(cls, line: str) -> "Job":
        # Format-string in squeue_user_jobs() decides field count + order.
        parts = line.split("|")
        if len(parts) < 7:
            raise ValueError(f"unexpected squeue row: {line!r}")
        return cls(
            job_id=parts[0],
            name=parts[1],
            state=parts[2],
            time_used=parts[3],
            time_left=parts[4],
            partition=parts[5],
            node_list=parts[6],
        )


class SlurmError(Exception):
    """Raised for any Slurm-side failure surfaced to the user."""


# --- runner ---------------------------------------------------------------


def real_runner(argv: list[str]) -> tuple[int, str, str]:
    """Default runner: actual subprocess.run."""
    res = subprocess.run(
        argv, capture_output=True, text=True, check=False
    )
    return res.returncode, res.stdout, res.stderr


# --- squeue ---------------------------------------------------------------


_SQUEUE_FORMAT = "%i|%j|%T|%M|%L|%P|%R"


def squeue_user_jobs(
    user: str | None = None,
    *,
    runner: Runner = real_runner,
) -> list[Job]:
    """Return the user's current jobs (running, pending, etc.)."""
    user = user or os.environ.get("USER") or ""
    argv = [
        "squeue",
        "-u",
        user,
        "-h",
        "-o",
        _SQUEUE_FORMAT,
    ]
    code, out, err = runner(argv)
    if code != 0:
        raise SlurmError(f"squeue failed: {err.strip() or out.strip()}")
    rows = [line for line in out.splitlines() if line.strip()]
    return [Job.from_squeue_row(line) for line in rows]


# --- jobid resolution -----------------------------------------------------


def resolve_jobid(
    arg: str | None,
    *,
    user: str | None = None,
    env: dict[str, str] | None = None,
    runner: Runner = real_runner,
) -> tuple[str, list[Job] | None]:
    """Resolve the jobid for `stop` / `jump` / `time` per the contract.

    Order: arg > $SLURM_JOB_ID > sole running solx-eligible job > ambiguous.

    Returns (jobid, None) on a clean resolve; (("", jobs)) on ambiguity so
    the caller can render a table and exit 2 deterministically.
    """
    if arg:
        return arg, None
    env = env if env is not None else dict(os.environ)
    if env.get("SLURM_JOB_ID"):
        return env["SLURM_JOB_ID"], None
    jobs = squeue_user_jobs(user=user, runner=runner)
    if not jobs:
        raise SlurmError("no jobs found for the current user")
    if len(jobs) == 1:
        return jobs[0].job_id, None
    return "", jobs


# --- salloc / scancel / srun argv builders -------------------------------


def salloc_argv(template: JobTemplate, passthrough: Iterable[str] = ()) -> list[str]:
    """Build the argv for `salloc --no-shell` from a template + CLI passthrough."""
    argv: list[str] = ["salloc", "--no-shell", "-J", f"solx-{template.name}"]
    argv += ["-p", template.partition, "-t", template.time]
    if template.qos:
        argv += ["-q", template.qos]
    if template.gres:
        argv += [f"--gres={template.gres}"]
    argv += list(template.extra_args)
    argv += list(passthrough)
    return argv


def scancel_argv(job_id: str) -> list[str]:
    return ["scancel", job_id]


def srun_pty_argv(job_id: str, shell: str) -> list[str]:
    """Argv for attaching a pty shell to a running allocation."""
    return ["srun", f"--jobid={job_id}", "--pty", shell]


def squeue_time_left_argv(job_id: str) -> list[str]:
    return ["squeue", "-h", "-j", job_id, "-O", "TimeLeft"]


# --- salloc execution ------------------------------------------------------


_GRANTED_RE = re.compile(r"Granted job allocation (\d+)")


def parse_granted_jobid(stderr_text: str) -> str:
    """Extract the jobid from `salloc`'s stderr `Granted job allocation N` line."""
    m = _GRANTED_RE.search(stderr_text)
    if not m:
        raise SlurmError(
            f"could not parse jobid from salloc output:\n{stderr_text}"
        )
    return m.group(1)


def run_salloc(
    argv: list[str],
    *,
    timeout_seconds: int,
    runner: Runner | None = None,
) -> str:
    """Invoke salloc and return the granted jobid.

    salloc --no-shell blocks until the allocation lands, then exits. If the
    queue stalls beyond `timeout_seconds`, we kill the process and surface
    a SlurmError so the user sees a clear timeout instead of a hang.
    """
    if runner is not None:
        # Test path: the runner returns the result directly. Timeout is
        # the caller's problem in that mode — tests inject deterministic
        # output without spawning subprocesses.
        code, _, err = runner(argv)
        if code != 0:
            raise SlurmError(f"salloc failed: {err.strip()}")
        return parse_granted_jobid(err)

    # Real path: subprocess with a wall-clock timeout.
    try:
        res = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        raise SlurmError(
            f"salloc timed out after {timeout_seconds}s waiting for the queue. "
            f"Cancel the request manually if needed; the request may still be "
            f"queued. Argv: {shlex.join(argv)}"
        ) from e

    if res.returncode != 0:
        raise SlurmError(
            f"salloc failed (exit {res.returncode}):\n{res.stderr.strip()}"
        )
    return parse_granted_jobid(res.stderr)

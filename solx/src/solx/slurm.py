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
#
# Resolution is VERB-AWARE. The conventions are inspired by tmux (a no-arg
# command acts on the obvious target; "most recent" when several exist; warn
# when you act on the session you're sitting in) but adapted to Slurm, where a
# cancelled job is unrecoverable and attaching spends real allocation time:
#
#   * `time`/`jump` (read / attach): when several jobs match, auto-pick the
#     MOST RECENT one (like `tmux attach`). Deterministic, so it's agent-safe.
#   * `stop` (cancel): NEVER auto-picks among several — that's how you cancel
#     the wrong job. It returns the candidates so the caller can print them and
#     exit 2. This is the deliberate divergence from tmux's "act on most recent".
#   * `jump`'s auto-pick considers RUNNING jobs only (you can't attach to a
#     pending one). An EXPLICIT arg or $SLURM_JOB_ID is passed through as-is
#     (no state pre-check) — by design, `srun` surfaces a wrong-state job far
#     more clearly than we could, and it saves a squeue round-trip.
#
# "Inside an allocation" ($SLURM_JOB_ID set) is treated as "the current
# session": it's the default target, and acting on it carries a nesting/
# self-cancel warning the caller surfaces.


VERB_JUMP = "jump"
VERB_STOP = "stop"
VERB_TIME = "time"


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving a jobid for one verb.

    Exactly one of these holds:
      * ``job_id`` is set        → resolved; act on it.
      * ``ambiguous`` is True    → several candidates, caller must disambiguate.
      * ``error`` is set         → nothing to act on (no jobs / none running).
    """

    job_id: str | None = None
    source: str = "arg"  # arg | inside | single | most-recent
    inside: bool = False  # $SLURM_JOB_ID is set (acting from within an allocation)
    inside_job_id: str | None = None
    candidates: tuple[Job, ...] = ()  # set considered (for ambiguity / context)
    ambiguous: bool = False
    error: str | None = None

    @property
    def acting_on_current(self) -> bool:
        """True when the resolved job is the one we're sitting inside."""
        return self.inside and self.job_id is not None and self.job_id == self.inside_job_id


def _jobid_key(job_id: str) -> tuple[int, int]:
    """Sort key making 'most recent' == 'highest job id'.

    Slurm assigns monotonically increasing ids, so the highest id is the
    newest submission — which for `solx job start` is the one you just made.
    Array ids like ``123_4`` sort by (base, index); a non-numeric id sorts
    first so a real number always wins.
    """
    base, _, idx = job_id.partition("_")
    try:
        return (int(base), int(idx) if idx.isdigit() else 0)
    except ValueError:
        return (-1, 0)


def most_recent(jobs: Iterable[Job]) -> Job:
    """Return the most recently submitted job (highest job id)."""
    return max(jobs, key=lambda j: _jobid_key(j.job_id))


def resolve_jobid(
    arg: str | None,
    *,
    verb: str = VERB_TIME,
    user: str | None = None,
    env: dict[str, str] | None = None,
    runner: Runner = real_runner,
) -> Resolution:
    """Resolve the jobid for `stop` / `jump` / `time`, verb-aware (see above).

    Order: explicit arg > inside-allocation ($SLURM_JOB_ID) > squeue. From
    squeue, a single candidate is used; several are auto-resolved to the most
    recent for read/attach verbs, or returned as ``ambiguous`` for ``stop``.

    Raises ``SlurmError`` if the squeue query fails (the explicit-arg and
    inside-allocation paths short-circuit before any squeue call, so they never
    raise). Every caller in ``jobs.py`` wraps this in try/except.
    """
    env = env if env is not None else dict(os.environ)
    inside_id = env.get("SLURM_JOB_ID") or None
    inside = inside_id is not None

    if arg:
        return Resolution(job_id=arg, source="arg", inside=inside, inside_job_id=inside_id)
    if inside_id:
        return Resolution(
            job_id=inside_id, source="inside", inside=True, inside_job_id=inside_id
        )

    jobs = squeue_user_jobs(user=user, runner=runner)
    candidates = [j for j in jobs if j.state == "RUNNING"] if verb == VERB_JUMP else jobs

    if not candidates:
        # For jump, distinguish "you have jobs but none running" from "no jobs".
        if verb == VERB_JUMP and jobs:
            err = "no running job to attach to (jobs exist but none are RUNNING)"
        else:
            err = "no jobs found for the current user"
        return Resolution(error=err, candidates=tuple(jobs), inside=inside)

    if len(candidates) == 1:
        return Resolution(
            job_id=candidates[0].job_id, source="single",
            candidates=tuple(candidates), inside=inside, inside_job_id=inside_id,
        )

    if verb == VERB_STOP:
        # Never auto-pick which job to cancel.
        return Resolution(
            ambiguous=True, candidates=tuple(candidates),
            inside=inside, inside_job_id=inside_id,
        )

    chosen = most_recent(candidates)
    return Resolution(
        job_id=chosen.job_id, source="most-recent",
        candidates=tuple(candidates), inside=inside, inside_job_id=inside_id,
    )


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
    """Argv for attaching a pty shell to a running allocation.

    `--overlap` lets the step share the allocation's resources with steps
    already running in it. Without it, srun demands exclusive use of the node
    and stalls with "step creation temporarily disabled (Requested nodes are
    busy)" whenever the job already has a step occupying its resources.
    """
    return ["srun", f"--jobid={job_id}", "--overlap", "--pty", shell]


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

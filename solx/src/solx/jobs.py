"""`solx job` subcommands: list, start, stop, jump, time.

Output obeys `solx.output.Out`: JSON on a non-TTY stdout, Rich tables on a
TTY, all diagnostics on stderr. Jobid resolution is verb-aware (see
`solx.slurm.resolve_jobid`): read/attach verbs auto-pick the most recent job,
the destructive `stop` never does, and acting from inside an allocation
carries a nesting / self-cancel guard.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import asdict
from typing import Iterable

from rich.prompt import Confirm
from rich.table import Table

from solx import slurm
from solx.config import Config, ConfigError
from solx.output import Out
from solx.slurm import Job, Resolution, SlurmError


# --- shared rendering -----------------------------------------------------


def _jobs_table(jobs: Iterable[Job]) -> Table:
    t = Table(title=None, show_lines=False, header_style="bold")
    for col in ("JOBID", "NAME", "STATE", "TIME", "LEFT", "PARTITION", "NODE / REASON"):
        t.add_column(col)
    for j in jobs:
        t.add_row(
            j.job_id, j.name, j.state, j.time_used, j.time_left,
            j.partition, j.node_list,
        )
    return t


def _jobs_payload(jobs: Iterable[Job]) -> list[dict]:
    return [asdict(j) for j in jobs]


def _print_candidates(out: Out, jobs: Iterable[Job], reason: str) -> None:
    """Surface a candidate set for a verb that won't auto-pick (stop)."""
    jobs = list(jobs)
    if out.json_mode:
        out.json({"error": reason, "jobs": _jobs_payload(jobs)})
    else:
        out.error(f"[yellow]{reason} — specify a JOBID:[/]")
        out.stderr.print(_jobs_table(jobs))


# --- list -----------------------------------------------------------------


def cmd_list(*, runner: slurm.Runner = slurm.real_runner, out: Out | None = None) -> int:
    out = out or Out.auto()
    try:
        jobs = slurm.squeue_user_jobs(runner=runner)
    except SlurmError as e:
        out.error(f"[red]error:[/] {e}")
        return 1
    out.emit(
        data=_jobs_payload(jobs),
        human=lambda: _jobs_table(jobs) if jobs else "[dim]no jobs in queue[/]",
    )
    return 0


# --- start ----------------------------------------------------------------


def cmd_start(
    *,
    config: Config,
    template_name: str | None,
    dry_run: bool,
    timeout_override: int | None,
    passthrough: list[str],
    salloc_runner: slurm.Runner | None = None,
    out: Out | None = None,
) -> int:
    out = out or Out.auto()
    name = template_name or config.default_template
    try:
        template = config.template(name)
    except ConfigError as e:
        out.error(f"[red]error:[/] {e}")
        return 1

    argv = slurm.salloc_argv(template, passthrough=passthrough)

    if dry_run:
        out.status("[bold]dry-run — would run:[/]")
        out.emit(
            data={"dry_run": True, "template": name, "argv": argv},
            human=lambda: f"  {shlex.join(argv)}",
        )
        return 0

    timeout = timeout_override or config.start_timeout_seconds
    out.status(f"[dim]submitting:[/] {shlex.join(argv)}")
    out.status(
        f"[dim]waiting up to {timeout}s for the queue to grant the allocation…[/]"
    )
    try:
        jobid = slurm.run_salloc(argv, timeout_seconds=timeout, runner=salloc_runner)
    except SlurmError as e:
        out.error(f"[red]error:[/] {e}")
        return 1

    out.status(f"[green]allocated job[/] [bold]{jobid}[/]")
    out.status(
        f"[dim]attach:[/] solx job jump  "
        f"[dim](or: srun --jobid={jobid} --pty {config.default_shell})[/]"
    )
    if out.json_mode:
        out.json({"jobid": jobid, "template": name})
    return 0


# --- stop -----------------------------------------------------------------


def cmd_stop(
    *,
    jobid_arg: str | None,
    yes: bool,
    dry_run: bool,
    runner: slurm.Runner = slurm.real_runner,
    out: Out | None = None,
    confirm_fn=None,
) -> int:
    out = out or Out.auto()
    if yes and dry_run:
        out.error("[red]error:[/] --yes and --dry-run are mutually exclusive")
        return 2

    try:
        res = slurm.resolve_jobid(jobid_arg, verb=slurm.VERB_STOP, runner=runner)
    except SlurmError as e:
        out.error(f"[red]error:[/] {e}")
        return 1
    if res.error:
        out.error(f"[red]error:[/] {res.error}")
        return 1
    if res.ambiguous:
        _print_candidates(out, res.candidates, "multiple jobs running")
        return 2

    jid = res.job_id
    argv = slurm.scancel_argv(jid)

    # Acting on the job you're sitting inside ends this session — surface it
    # in every path, including a dry-run preview, so the resolver's decision is
    # never a surprise.
    self_cancel = res.acting_on_current
    if self_cancel:
        out.status(
            f"[yellow]warning:[/] job {jid} is the allocation you're inside "
            "($SLURM_JOB_ID); cancelling it will end this session."
        )

    if dry_run:
        out.status("[bold]dry-run — would run:[/]")
        out.emit(
            data={
                "dry_run": True,
                "jobid": jid,
                "argv": argv,
                "inside_allocation": self_cancel,
            },
            human=lambda: f"  {shlex.join(argv)}",
        )
        return 0

    if not yes:
        if not out.interactive:
            out.error(
                "[red]error:[/] non-interactive session — pass -y to cancel "
                f"job {jid}, or -n to preview."
            )
            return 2
        ask = confirm_fn or Confirm.ask
        prompt = (
            f"Cancel job {jid} (the one you're inside)?"
            if self_cancel
            else f"Cancel job {jid}?"
        )
        if not ask(prompt, default=False):
            out.status("[dim]aborted[/]")
            return 1

    code, _, err = runner(argv)
    if code != 0:
        out.error(f"[red]scancel failed:[/] {err.strip()}")
        return 1
    out.status(f"[green]cancelled[/] job {jid}")
    if out.json_mode:
        out.json({"cancelled": jid})
    return 0


# --- jump -----------------------------------------------------------------


def cmd_jump(
    *,
    config: Config,
    jobid_arg: str | None,
    quiet: bool = False,
    runner: slurm.Runner = slurm.real_runner,
    exec_fn=None,
    out: Out | None = None,
) -> int:
    """Drop the user into a shell on the job's compute node.

    Exec-replaces the current process with `srun --pty` so the user's shell
    history and signal handling are clean. Tests inject `exec_fn` to capture
    argv without exec'ing.

    Nesting heads-up: attaching from *inside* an allocation ($SLURM_JOB_ID set)
    spawns a nested step. Unlike `stop`, attach is non-destructive and
    Ctrl-D-recoverable, so we WARN-AND-PROCEED (not refuse) — `-q/--quiet`
    silences the heads-up.
    """
    out = out or Out.auto()
    try:
        res = slurm.resolve_jobid(jobid_arg, verb=slurm.VERB_JUMP, runner=runner)
    except SlurmError as e:
        out.error(f"[red]error:[/] {e}")
        return 1
    if res.error:
        out.error(f"[red]error:[/] {res.error}")
        return 1

    if not quiet:
        if res.acting_on_current:
            out.status(
                f"[yellow]already inside job {res.inside_job_id}[/] — opening a "
                "nested srun step here burns extra resources. `exit` to leave, "
                "or pass another JOBID. Attaching anyway."
            )
        elif res.inside:
            out.status(
                f"[yellow]nesting:[/] you're inside job {res.inside_job_id}; "
                f"attaching to job {res.job_id} opens a step on another "
                "allocation. Proceeding."
            )
        if res.source == "most-recent":
            out.status(
                f"[dim]multiple running jobs; attaching to most recent "
                f"{res.job_id} (pass JOBID to choose another)[/]"
            )

    jid = res.job_id
    argv = slurm.srun_pty_argv(jid, config.default_shell)
    if exec_fn is not None:
        exec_fn(argv)
        return 0

    os.execvp(argv[0], argv)
    return 0  # unreachable


# --- time -----------------------------------------------------------------


def cmd_time(
    *,
    jobid_arg: str | None,
    runner: slurm.Runner = slurm.real_runner,
    out: Out | None = None,
) -> int:
    out = out or Out.auto()
    try:
        res = slurm.resolve_jobid(jobid_arg, verb=slurm.VERB_TIME, runner=runner)
    except SlurmError as e:
        out.error(f"[red]error:[/] {e}")
        return 1
    if res.error:
        out.error(f"[red]error:[/] {res.error}")
        return 1
    if res.source == "most-recent":
        out.status(
            f"[dim]multiple jobs; showing most recent {res.job_id} "
            "(pass JOBID to choose another)[/]"
        )

    jid = res.job_id
    argv = slurm.squeue_time_left_argv(jid)
    code, out_text, err = runner(argv)
    if code != 0 or not out_text.strip():
        out.error(
            f"[red]squeue failed for jobid {jid}:[/] "
            f"{err.strip() or '(empty output)'}"
        )
        return 1
    time_left = out_text.strip()
    out.emit(data={"jobid": jid, "time_left": time_left}, human=lambda: time_left)
    return 0

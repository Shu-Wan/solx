"""`solx job` subcommands: list, start, stop, jump, time."""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Iterable

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from solx import slurm
from solx.config import Config, ConfigError
from solx.slurm import Job, SlurmError


# --- list -----------------------------------------------------------------


def cmd_list(*, runner: slurm.Runner = slurm.real_runner, console: Console | None = None) -> int:
    console = console or Console()
    try:
        jobs = slurm.squeue_user_jobs(runner=runner)
    except SlurmError as e:
        console.print(f"[red]error:[/] {e}")
        return 1
    if not jobs:
        console.print("[dim]no jobs in queue[/]")
        return 0
    console.print(_jobs_table(jobs))
    return 0


def _jobs_table(jobs: Iterable[Job]) -> Table:
    t = Table(title=None, show_lines=False, header_style="bold")
    t.add_column("JOBID")
    t.add_column("NAME")
    t.add_column("STATE")
    t.add_column("TIME")
    t.add_column("LEFT")
    t.add_column("PARTITION")
    t.add_column("NODE / REASON")
    for j in jobs:
        t.add_row(
            j.job_id, j.name, j.state, j.time_used, j.time_left,
            j.partition, j.node_list,
        )
    return t


# --- start ----------------------------------------------------------------


def cmd_start(
    *,
    config: Config,
    template_name: str | None,
    dry_run: bool,
    timeout_override: int | None,
    passthrough: list[str],
    salloc_runner: slurm.Runner | None = None,
    console: Console | None = None,
) -> int:
    console = console or Console()
    name = template_name or config.default_template
    try:
        template = config.template(name)
    except ConfigError as e:
        console.print(f"[red]error:[/] {e}")
        return 1

    argv = slurm.salloc_argv(template, passthrough=passthrough)

    if dry_run:
        console.print("[bold]dry-run — would run:[/]")
        console.print(f"  {shlex.join(argv)}")
        return 0

    timeout = timeout_override or config.start_timeout_seconds
    console.print(
        f"[dim]submitting:[/] {shlex.join(argv)}",
    )
    console.print(
        f"[dim]waiting up to {timeout}s for the queue to grant the allocation…[/]"
    )
    try:
        jobid = slurm.run_salloc(
            argv, timeout_seconds=timeout, runner=salloc_runner
        )
    except SlurmError as e:
        console.print(f"[red]error:[/] {e}")
        return 1

    console.print(f"[green]allocated job[/] [bold]{jobid}[/]")
    console.print(
        f"[dim]attach:[/] solx job jump  "
        f"[dim](or: srun --jobid={jobid} --pty {config.default_shell})[/]"
    )
    return 0


# --- stop -----------------------------------------------------------------


def cmd_stop(
    *,
    jobid_arg: str | None,
    yes: bool,
    dry_run: bool,
    runner: slurm.Runner = slurm.real_runner,
    console: Console | None = None,
    confirm_fn=None,
) -> int:
    console = console or Console()
    if yes and dry_run:
        console.print("[red]error:[/] --yes and --dry-run are mutually exclusive")
        return 2

    try:
        jid, ambiguous = slurm.resolve_jobid(jobid_arg, runner=runner)
    except SlurmError as e:
        console.print(f"[red]error:[/] {e}")
        return 1
    if ambiguous is not None:
        console.print("[yellow]multiple jobs running. specify a JOBID:[/]")
        console.print(_jobs_table(ambiguous))
        return 2

    argv = slurm.scancel_argv(jid)
    if dry_run:
        console.print("[bold]dry-run — would run:[/]")
        console.print(f"  {shlex.join(argv)}")
        return 0
    if not yes:
        ask = confirm_fn or Confirm.ask
        if not ask(f"Cancel job {jid}?", default=False):
            console.print("[dim]aborted[/]")
            return 1

    code, _, err = runner(argv)
    if code != 0:
        console.print(f"[red]scancel failed:[/] {err.strip()}")
        return 1
    console.print(f"[green]cancelled[/] job {jid}")
    return 0


# --- jump -----------------------------------------------------------------


def cmd_jump(
    *,
    config: Config,
    jobid_arg: str | None,
    runner: slurm.Runner = slurm.real_runner,
    exec_fn=None,
    console: Console | None = None,
) -> int:
    """Drop the user into a shell on the job's compute node.

    Exec-replaces the current process with `srun --pty` so the user's
    shell history and signal handling are clean. Tests inject `exec_fn`
    to capture argv without actually exec'ing.
    """
    console = console or Console()
    try:
        jid, ambiguous = slurm.resolve_jobid(jobid_arg, runner=runner)
    except SlurmError as e:
        console.print(f"[red]error:[/] {e}")
        return 1
    if ambiguous is not None:
        console.print("[yellow]multiple jobs running. specify a JOBID:[/]")
        console.print(_jobs_table(ambiguous))
        return 2

    argv = slurm.srun_pty_argv(jid, config.default_shell)
    if exec_fn is not None:
        exec_fn(argv)
        return 0

    # Real path: replace this process with srun.
    os.execvp(argv[0], argv)
    return 0  # unreachable


# --- time -----------------------------------------------------------------


def cmd_time(
    *,
    jobid_arg: str | None,
    runner: slurm.Runner = slurm.real_runner,
    console: Console | None = None,
) -> int:
    console = console or Console()
    try:
        jid, ambiguous = slurm.resolve_jobid(jobid_arg, runner=runner)
    except SlurmError as e:
        console.print(f"[red]error:[/] {e}")
        return 1
    if ambiguous is not None:
        console.print("[yellow]multiple jobs running. specify a JOBID:[/]")
        console.print(_jobs_table(ambiguous))
        return 2

    argv = slurm.squeue_time_left_argv(jid)
    code, out, err = runner(argv)
    if code != 0 or not out.strip():
        console.print(f"[red]squeue failed for jobid {jid}:[/] {err.strip() or '(empty output)'}")
        return 1
    console.print(out.strip())
    return 0

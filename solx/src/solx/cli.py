"""Typer entry point for `solx`.

Surface (per `docs/stage-2-solx.md`):

    solx init
    solx job list  (alias `ls`; group also reachable as `jobs`)
    solx job start [TEMPLATE]
    solx job stop  [JOBID]
    solx job jump  [JOBID]    (also `solx jump`)
    solx job time  [JOBID]
    solx keep      [--stage S] [--csv-dir D] [-j N] [-y] [-n] [-v]
    solx config show
    solx config edit
    solx completions <bash|zsh|fish>
    solx --version
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer

from solx import __version__
from solx import config as cfg
from solx import init as init_mod
from solx import jobs as jobs_mod
from solx import keep as keep_mod
from solx.config import ConfigError
from solx.side import require_sol


# --- root + groups --------------------------------------------------------

app = typer.Typer(
    name="solx",
    help="Sol-first CLI for ASU's Sol supercomputer.",
    no_args_is_help=True,
    add_completion=False,
)

job_app = typer.Typer(
    name="job",
    help="Manage interactive Slurm jobs on Sol.",
    no_args_is_help=True,
)
# Both `job` and `jobs` reach the same subgroup.
app.add_typer(job_app, name="job")
app.add_typer(job_app, name="jobs")

config_app = typer.Typer(
    name="config",
    help="Inspect and edit the solx config.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """Sol-first CLI."""


# --- top-level: init ------------------------------------------------------


@app.command("init", help="Write a starter config.toml.")
def init_cmd(
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite without prompting."),
    ] = False,
) -> None:
    require_sol()
    raise typer.Exit(code=init_mod.cmd_init(force=force))


# --- top-level: keep ------------------------------------------------------


@app.command(
    "keep",
    help="Renew CSV-flagged scratch files filtered by the keep block in config.",
)
def keep_cmd(
    stage: Annotated[
        str,
        typer.Option(
            "--stage",
            help="Which warning CSVs to read.",
            case_sensitive=False,
        ),
    ] = "all",
    csv_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--csv-dir",
            help="Directory holding Sol's warning CSVs.",
            exists=False,
        ),
    ] = None,
    jobs_n: Annotated[
        int,
        typer.Option(
            "-j",
            "--jobs",
            help="Parallel touch workers.",
            min=1,
        ),
    ] = max(1, min(8, (os.cpu_count() or 2) // 4)),
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print plan without executing."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Verbose plan + progress."),
    ] = False,
) -> None:
    require_sol()
    valid_stages = {"all", *keep_mod.STAGE_ORDER}
    if stage not in valid_stages:
        typer.echo(
            f"invalid --stage {stage!r}. choose from: {', '.join(sorted(valid_stages))}",
            err=True,
        )
        raise typer.Exit(code=2)
    config = _load_or_exit()
    code = keep_mod.cmd_keep(
        config=config,
        csv_dir=csv_dir,
        stage=stage,
        jobs_n=jobs_n,
        yes=yes,
        dry_run=dry_run,
        verbose=verbose,
    )
    raise typer.Exit(code=code)


# --- top-level: jump (shortcut for `job jump`) ----------------------------


@app.command(
    "jump",
    help="Drop into a shell on the job's compute node (= solx job jump).",
)
def jump_cmd(
    jobid: Annotated[
        Optional[str],
        typer.Argument(help="Job ID. Defaults to current job (compute) or sole running job (login)."),
    ] = None,
) -> None:
    require_sol()
    config = _load_or_exit()
    raise typer.Exit(code=jobs_mod.cmd_jump(config=config, jobid_arg=jobid))


# --- job subcommands ------------------------------------------------------


@job_app.command("list", help="Print my Sol jobs.")
def job_list_cmd() -> None:
    require_sol()
    raise typer.Exit(code=jobs_mod.cmd_list())


@job_app.command("ls", help="Alias for `solx job list`.", hidden=True)
def job_ls_cmd() -> None:
    require_sol()
    raise typer.Exit(code=jobs_mod.cmd_list())


@job_app.command(
    "start",
    help="Start an interactive allocation from a config template.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def job_start_cmd(
    ctx: typer.Context,
    template: Annotated[
        Optional[str],
        typer.Argument(help="Template name; defaults to default_template."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print salloc argv without submitting."),
    ] = False,
    timeout: Annotated[
        Optional[str],
        typer.Option(
            "--timeout",
            help="Override start_timeout (e.g. \"5m\", \"1h\").",
        ),
    ] = None,
) -> None:
    require_sol()
    config = _load_or_exit()
    timeout_seconds: Optional[int] = None
    if timeout:
        try:
            timeout_seconds = cfg.parse_duration(timeout)
        except ConfigError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=2)
    raise typer.Exit(
        code=jobs_mod.cmd_start(
            config=config,
            template_name=template,
            dry_run=dry_run,
            timeout_override=timeout_seconds,
            passthrough=list(ctx.args),
        )
    )


@job_app.command("stop", help="Cancel a job (prompts unless -y).")
def job_stop_cmd(
    jobid: Annotated[
        Optional[str],
        typer.Argument(help="Job ID. Defaults per resolution rules."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print scancel argv without executing."),
    ] = False,
) -> None:
    require_sol()
    raise typer.Exit(
        code=jobs_mod.cmd_stop(jobid_arg=jobid, yes=yes, dry_run=dry_run)
    )


@job_app.command("jump", help="Drop into a shell on the job's compute node.")
def job_jump_cmd(
    jobid: Annotated[
        Optional[str],
        typer.Argument(help="Job ID. Defaults per resolution rules."),
    ] = None,
) -> None:
    require_sol()
    config = _load_or_exit()
    raise typer.Exit(code=jobs_mod.cmd_jump(config=config, jobid_arg=jobid))


@job_app.command("time", help="Print remaining time (D-HH:MM:SS).")
def job_time_cmd(
    jobid: Annotated[
        Optional[str],
        typer.Argument(help="Job ID. Defaults per resolution rules."),
    ] = None,
) -> None:
    require_sol()
    raise typer.Exit(code=jobs_mod.cmd_time(jobid_arg=jobid))


# --- config subcommands ---------------------------------------------------


@config_app.command("show", help="Print the resolved config.")
def config_show_cmd(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit JSON.")
    ] = False,
) -> None:
    require_sol()
    config = _load_or_exit()
    if json_output:
        import json
        from dataclasses import asdict

        # KeepRules has compiled pathspec objects; serialize the raw inputs only.
        data = {
            "default_shell": config.default_shell,
            "default_template": config.default_template,
            "start_timeout_seconds": config.start_timeout_seconds,
            "templates": {
                name: {
                    k: v
                    for k, v in asdict(t).items()
                    if v not in (None, ())
                }
                for name, t in config.templates.items()
            },
            "keep": (
                {
                    "include": list(config.keep.raw_include),
                    "exclude": list(config.keep.raw_exclude),
                }
                if config.keep is not None
                else None
            ),
        }
        typer.echo(json.dumps(data, indent=2))
        return

    from rich.console import Console
    from rich.table import Table

    c = Console()
    c.print(f"[bold]default_shell[/]    {config.default_shell}")
    c.print(f"[bold]default_template[/] {config.default_template}")
    c.print(f"[bold]start_timeout[/]    {config.start_timeout_seconds}s")

    for name, t in config.templates.items():
        tbl = Table(
            title=rf"\[jobs.{name}]",
            show_header=False,
            title_justify="left",
        )
        tbl.add_row("partition", t.partition)
        tbl.add_row("time", t.time)
        if t.qos:
            tbl.add_row("qos", t.qos)
        if t.gres:
            tbl.add_row("gres", t.gres)
        if t.extra_args:
            tbl.add_row("extra_args", " ".join(t.extra_args))
        c.print(tbl)

    if config.keep is not None:
        tbl = Table(
            title=r"\[keep]", show_header=False, title_justify="left"
        )
        tbl.add_row("include", "\n".join(config.keep.raw_include))
        if config.keep.raw_exclude:
            tbl.add_row("exclude", "\n".join(config.keep.raw_exclude))
        c.print(tbl)
    else:
        c.print(r"[dim]\[keep] not configured (solx keep will exit 2)[/]")


@config_app.command("edit", help="Open the config in $EDITOR.")
def config_edit_cmd() -> None:
    require_sol()
    p = cfg.config_path()
    if not p.exists():
        typer.echo(
            f"no config at {p}. run `solx init` first.",
            err=True,
        )
        raise typer.Exit(code=2)
    editor = os.environ.get("EDITOR") or shutil.which("vi") or "nano"
    raise typer.Exit(code=subprocess.call([editor, str(p)]))


# --- completions ----------------------------------------------------------


@app.command(
    "completions",
    help="Emit a shell completion script (bash, zsh, or fish).",
)
def completions_cmd(
    shell: Annotated[
        str,
        typer.Argument(help="Target shell: bash, zsh, or fish."),
    ],
) -> None:
    """Friendlier alias for Typer's --show-completion machinery.

    Defers to Click's completion script generation under the hood.
    """
    shell = shell.lower()
    if shell not in {"bash", "zsh", "fish"}:
        typer.echo(
            f"unknown shell {shell!r}; choose bash, zsh, or fish.",
            err=True,
        )
        raise typer.Exit(code=2)
    # Re-invoke ourselves with Typer/Click's --show-completion=<shell>.
    # We can't easily call into Typer's completion API directly, so shell out
    # to the same binary; on the second call Typer prints the script and exits.
    import sys

    env = dict(os.environ)
    env["_SOLX_COMPLETE"] = f"{shell}_source"
    res = subprocess.run([sys.argv[0]], env=env, capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout:
        # Fallback: emit a minimal hint pointing at Typer's built-in.
        typer.echo(
            f"# To install solx completions for {shell}, run:\n"
            f"#   solx --show-completion {shell}",
            err=False,
        )
        raise typer.Exit(code=res.returncode)
    typer.echo(res.stdout)


# --- helpers --------------------------------------------------------------


def _load_or_exit():
    try:
        return cfg.load()
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2)

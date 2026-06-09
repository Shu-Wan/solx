"""Typer entry point for `solx`.

Surface (see docs/solx.md):

    solx init
    solx job list  (alias `ls`; group also reachable as `jobs`)
    solx job start [TEMPLATE]
    solx job stop  [JOBID]
    solx job jump  [JOBID] [--force]   (also `solx jump`)
    solx job time  [JOBID]
    solx keep      [--stage S] [--csv-dir D] [-j N] [-y] [-n] [-v]
    solx config show [--json]
    solx config edit
    solx config import-solkeep   (migrate ~/.solkeep into [keep])
    solx completions <bash|zsh|fish>
    solx version   (alias of --version)
    solx help      (alias of --help)

Global output flag: `--json` forces JSON; by default output auto-detects
(Rich tables on a terminal, JSON when stdout is not a TTY). See `solx.output`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import typer

from solx import __version__
from solx.side import require_sol

if TYPE_CHECKING:
    from solx import output
    from solx.output import Out

# solx's home lives on NFS, where every module import is a network round-trip,
# so a cold `solx --version` pays for whatever this module pulls in. Command
# implementations (and their rich/pathspec dependency trees) are therefore
# imported inside the command bodies, not here — only typer and the cheap
# `solx.side` check load up front.


# --- root + groups --------------------------------------------------------

app = typer.Typer(
    name="solx",
    help="CLI for ASU's Sol supercomputer.",
    no_args_is_help=True,
    add_completion=False,
)

# `add_completion=False` keeps the --install/--show-completion flags off the
# root command, but it also means Typer never registers its bash/zsh/fish
# completion classes. Without them, the runtime `_SOLX_COMPLETE=complete_<shell>`
# dispatch resolves no class and reports "Shell <shell> not supported", so Tab
# does nothing. Register them at import (before app() runs) so completion fires.
try:
    from typer.completion import completion_init

    completion_init()
except ImportError:  # Typer internals shifted under an unpinned upgrade
    pass  # completion won't fire, but the rest of the CLI is unaffected

job_app = typer.Typer(
    name="job",
    help="Manage interactive Slurm jobs on Sol (alias: jobs).",
    no_args_is_help=True,
)
# Both `job` and `jobs` reach the same subgroup; only `job` is listed in
# --help (one line per command, aliases noted inline).
app.add_typer(job_app, name="job")
app.add_typer(job_app, name="jobs", hidden=True)

config_app = typer.Typer(
    name="config",
    help="Inspect and edit the solx config.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


# Output format forced by the global --json flag. None == auto-detect.
_FORCE: Optional[output.Force] = None


def _out() -> Out:
    """Build the resolved output target for a command body."""
    from solx.output import Out

    return Out.auto(force=_FORCE)


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
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Force JSON output (machine-readable)."),
    ] = False,
) -> None:
    """The solx CLI.

    Output auto-detects: Rich tables on a terminal, JSON when stdout is not a
    TTY. A human at a terminal gets tables with no flag; an agent passes
    `--json` to force JSON anywhere. Diagnostics always go to stderr.
    """
    global _FORCE
    _FORCE = "json" if json_out else None


# --- top-level: init ------------------------------------------------------


@app.command("init", help="Write a starter config.toml.")
def init_cmd(
    force: Annotated[
        bool,
        typer.Option(
            "--force", "-f", "--yes", "-y",
            help="Overwrite without prompting (-y/--yes accepted too).",
        ),
    ] = False,
) -> None:
    require_sol()
    from solx import init as init_mod

    # Auto-import an existing ~/.solkeep into the new config's [keep] block.
    raise typer.Exit(
        code=init_mod.cmd_init(force=force, solkeep=Path.home() / ".solkeep", out=_out())
    )


# --- top-level: keep ------------------------------------------------------


@app.command(
    "keep",
    help="Renew CSV-flagged scratch files filtered by the keep block in config.",
)
def keep_cmd(
    stage: Annotated[
        str,
        typer.Option("--stage", help="Which warning CSVs to read.", case_sensitive=False),
    ] = "all",
    csv_dir: Annotated[
        Optional[Path],
        typer.Option("--csv-dir", help="Directory holding Sol's warning CSVs.", exists=False),
    ] = None,
    solkeep: Annotated[
        Optional[Path],
        typer.Option(
            "--solkeep",
            help="Path to a gitignore-style keep-list (overrides the [keep] config block).",
        ),
    ] = None,
    jobs_n: Annotated[
        int,
        typer.Option("-j", "--jobs", help="Parallel touch workers.", min=1),
    ] = max(1, min(8, (os.cpu_count() or 2) // 4)),
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", "--force", "-f", help="Skip confirmation prompt (also -f/--force)."),
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
    from solx import config as cfg
    from solx import keep as keep_mod

    out = _out()
    valid_stages = {"all", *keep_mod.STAGE_ORDER}
    if stage not in valid_stages:
        out.error(
            f"invalid --stage {stage!r}. choose from: {', '.join(sorted(valid_stages))}"
        )
        raise typer.Exit(code=2)
    # `keep` can run off a `~/.solkeep` alone, so a missing config.toml is fine
    # (config stays None). A config that exists but is malformed still errors.
    config = _load_or_exit(out) if cfg.config_path().exists() else None
    code = keep_mod.cmd_keep(
        config=config,
        csv_dir=csv_dir,
        stage=stage,
        jobs_n=jobs_n,
        yes=yes,
        dry_run=dry_run,
        verbose=verbose,
        solkeep=solkeep,
        out=out,
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
        typer.Argument(help="Job ID. Defaults to current job (compute) or sole/most-recent running job (login)."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress the nesting / most-recent heads-up."),
    ] = False,
) -> None:
    require_sol()
    from solx import jobs as jobs_mod

    out = _out()
    config = _load_or_exit(out)
    raise typer.Exit(code=jobs_mod.cmd_jump(config=config, jobid_arg=jobid, quiet=quiet, out=out))


# --- job subcommands ------------------------------------------------------


@job_app.command("list", help="Print my Sol jobs.")
def job_list_cmd() -> None:
    require_sol()
    from solx import jobs as jobs_mod

    raise typer.Exit(code=jobs_mod.cmd_list(out=_out()))


@job_app.command("ls", help="Alias for `solx job list`.", hidden=True)
def job_ls_cmd() -> None:
    require_sol()
    from solx import jobs as jobs_mod

    raise typer.Exit(code=jobs_mod.cmd_list(out=_out()))


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
        typer.Option("--timeout", help='Override start_timeout (e.g. "5m", "1h").'),
    ] = None,
) -> None:
    require_sol()
    from solx import config as cfg
    from solx import jobs as jobs_mod
    from solx.config import ConfigError

    out = _out()
    config = _load_or_exit(out)
    timeout_seconds: Optional[int] = None
    if timeout:
        try:
            timeout_seconds = cfg.parse_duration(timeout)
        except ConfigError as e:
            out.error(f"error: {e}")
            raise typer.Exit(code=2)
    raise typer.Exit(
        code=jobs_mod.cmd_start(
            config=config,
            template_name=template,
            dry_run=dry_run,
            timeout_override=timeout_seconds,
            passthrough=list(ctx.args),
            out=out,
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
        typer.Option("--yes", "-y", "--force", "-f", help="Skip confirmation prompt (also -f/--force)."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print scancel argv without executing."),
    ] = False,
) -> None:
    require_sol()
    from solx import jobs as jobs_mod

    raise typer.Exit(
        code=jobs_mod.cmd_stop(jobid_arg=jobid, yes=yes, dry_run=dry_run, out=_out())
    )


@job_app.command("jump", help="Drop into a shell on the job's compute node.")
def job_jump_cmd(
    jobid: Annotated[
        Optional[str],
        typer.Argument(help="Job ID. Defaults per resolution rules."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress the nesting / most-recent heads-up."),
    ] = False,
) -> None:
    require_sol()
    from solx import jobs as jobs_mod

    out = _out()
    config = _load_or_exit(out)
    raise typer.Exit(code=jobs_mod.cmd_jump(config=config, jobid_arg=jobid, quiet=quiet, out=out))


@job_app.command("time", help="Print remaining time (D-HH:MM:SS).")
def job_time_cmd(
    jobid: Annotated[
        Optional[str],
        typer.Argument(help="Job ID. Defaults per resolution rules."),
    ] = None,
) -> None:
    require_sol()
    from solx import jobs as jobs_mod

    raise typer.Exit(code=jobs_mod.cmd_time(jobid_arg=jobid, out=_out()))


# --- config subcommands ---------------------------------------------------


@config_app.command("show", help="Print the resolved config.")
def config_show_cmd(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit JSON.")
    ] = False,
) -> None:
    require_sol()
    out = _out()
    config = _load_or_exit(out)
    as_json = json_output or out.json_mode

    if as_json:
        from dataclasses import asdict

        # KeepRules holds compiled pathspec objects; serialize raw inputs only.
        data = {
            "default_shell": config.default_shell,
            "default_template": config.default_template,
            "start_timeout_seconds": config.start_timeout_seconds,
            "templates": {
                name: {k: v for k, v in asdict(t).items() if v not in (None, ())}
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
        out.json(data)
        return

    from rich.table import Table

    c = out.stdout
    c.print(f"[bold]default_shell[/]    {config.default_shell}")
    c.print(f"[bold]default_template[/] {config.default_template}")
    c.print(f"[bold]start_timeout[/]    {config.start_timeout_seconds}s")

    for name, t in config.templates.items():
        tbl = Table(title=rf"\[jobs.{name}]", show_header=False, title_justify="left")
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
        tbl = Table(title=r"\[keep]", show_header=False, title_justify="left")
        tbl.add_row("include", "\n".join(config.keep.raw_include))
        if config.keep.raw_exclude:
            tbl.add_row("exclude", "\n".join(config.keep.raw_exclude))
        c.print(tbl)
    else:
        c.print(r"[dim]\[keep] not configured (solx keep will exit 2)[/]")


@config_app.command("edit", help="Open the config in $EDITOR.")
def config_edit_cmd() -> None:
    require_sol()
    import shlex
    import shutil
    import subprocess

    from solx import config as cfg

    p = cfg.config_path()
    if not p.exists():
        typer.echo(f"no config at {p}. run `solx init` first.", err=True)
        raise typer.Exit(code=2)
    # $EDITOR is often a command with flags (e.g. "code --wait", "vim -u NORC"),
    # so split it into argv rather than treating the whole string as one binary.
    editor = os.environ.get("EDITOR") or shutil.which("vi") or "nano"
    editor_argv = shlex.split(editor)
    raise typer.Exit(code=subprocess.call([*editor_argv, str(p)]))


@config_app.command(
    "import-solkeep",
    help="Migrate a legacy ~/.solkeep keep-list into the config's [keep] block.",
)
def config_import_solkeep_cmd(
    solkeep: Annotated[
        Optional[Path],
        typer.Option(
            "--solkeep",
            help="Keep-list to import (default: ~/.solkeep).",
        ),
    ] = None,
) -> None:
    require_sol()
    from solx import init as init_mod

    raise typer.Exit(code=init_mod.cmd_import_solkeep(solkeep=solkeep, out=_out()))


# --- completions ----------------------------------------------------------

# Click >= 8.x's dual-mode zsh footer. Typer renders its own older zsh
# template that ends with a bare `compdef`, which only registers the
# completer — fine when the script is eval'd/sourced after compinit, but
# installed on fpath (`solx completions zsh > ~/.zfunc/_solx`) compinit
# autoloads the file body *as* the completer, so the function must also be
# called or the first Tab of a session produces nothing.
_ZSH_BARE_COMPDEF = "compdef _solx_completion solx"
_ZSH_DUAL_MODE_FOOTER = """\
if [[ $zsh_eval_context[-1] == loadautofunc ]]; then
    # autoload from fpath, call function directly
    _solx_completion "$@"
else
    # eval/source/. command, register function for later
    compdef _solx_completion solx
fi"""


def _zsh_dual_mode(script: str) -> str:
    """Swap the trailing bare ``compdef`` for the dual-mode footer.

    If the script doesn't end with the expected line (a Typer upgrade
    changed the template), return it unmodified — eval/source installs
    keep working either way.
    """
    head, sep, tail = script.rpartition(_ZSH_BARE_COMPDEF)
    if not sep or tail.strip():
        return script
    return head + _ZSH_DUAL_MODE_FOOTER + tail


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
    """Print a completion script for `shell`.

    Built with Typer's completion-script generator — the same one behind
    Typer's ``--show-completion`` — so the emitted script carries the right
    env-var wiring (``_SOLX_COMPLETE``, ``_TYPER_COMPLETE_ARGS``) and matches
    Typer's runtime completion handler regardless of how click is packaged.
    No re-exec, so it works under both the installed `solx` entry point and
    `python -m solx`.

    The zsh script gets a dual-mode footer so both install modes work:
    eval/source (compdef registers the completer) and fpath/autoload
    (`solx completions zsh > ~/.zfunc/_solx`, where the autoloaded body
    must call the completer itself).
    """
    shell = shell.lower()
    if shell not in {"bash", "zsh", "fish"}:
        typer.echo(f"unknown shell {shell!r}; choose bash, zsh, or fish.", err=True)
        raise typer.Exit(code=2)
    try:
        from typer.completion import get_completion_script
    except ImportError as e:  # Typer internals shifted under an unpinned upgrade
        typer.echo(f"solx: completion unavailable with this Typer ({e}).", err=True)
        raise typer.Exit(code=1)
    script = get_completion_script(
        prog_name="solx", complete_var="_SOLX_COMPLETE", shell=shell
    )
    if shell == "zsh":
        script = _zsh_dual_mode(script)
    typer.echo(script)


# --- meta: version / help -------------------------------------------------


@app.command("version", help="Show version and exit (alias of --version).")
def version_cmd() -> None:
    typer.echo(__version__)


@app.command("help", help="Show help and exit (alias of --help).")
def help_cmd(ctx: typer.Context) -> None:
    # The root group's help, matching `solx --help`.
    typer.echo((ctx.parent or ctx).get_help())


# --- helpers --------------------------------------------------------------


def _load_or_exit(out: Out | None = None):
    from solx import config as cfg
    from solx.config import ConfigError

    try:
        return cfg.load()
    except ConfigError as e:
        (out or _out()).error(f"error: {e}")
        raise typer.Exit(code=2)

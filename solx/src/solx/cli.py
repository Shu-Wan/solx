"""Typer entry point for `solx`."""
from __future__ import annotations

from typing import Annotated, Optional

import typer

from solx import __version__
from solx import side as side_mod
from solx import sol_cmds


app = typer.Typer(
    name="solx",
    help="Sol-side session and config CLI for ASU's Sol supercomputer.",
    no_args_is_help=True,
    add_completion=False,
)

session_app = typer.Typer(
    name="session",
    help="Manage Slurm allocations on Sol.",
    no_args_is_help=True,
)
app.add_typer(session_app, name="session")

config_app = typer.Typer(
    name="config",
    help="Manage solx profile config.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


# Stage 2a only ships the Sol side. Everything that needs to run on a laptop
# exits with this message so users don't accidentally rely on placeholders.
_LAPTOP_DEFERRED_MSG = (
    "This command is deferred to a future release (laptop-side `solx`).\n"
    "Until then: ssh to Sol manually, then run `solx` there."
)


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
    """Sol-side CLI."""


def _require_sol() -> None:
    if side_mod.detect() != "sol":
        typer.echo(
            "solx subcommands run on Sol only in this release. "
            "SSH to Sol first, then re-run.",
            err=True,
        )
        raise typer.Exit(code=2)


def _laptop_stub() -> None:
    typer.echo(_LAPTOP_DEFERRED_MSG, err=True)
    raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# Universal
# ---------------------------------------------------------------------------


@app.command()
def where() -> None:
    """Print which side (Sol or not-Sol) you're on."""
    s = side_mod.detect()
    node = side_mod.current_node()
    if s == "sol":
        typer.echo(f"sol mode ({node})")
    else:
        typer.echo(
            f"not-sol mode ({node}) — solx is only useful on Sol in this release."
        )


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


@session_app.command(
    "start",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Allocate a Slurm session via sbatch and record session.json.",
)
def session_start_cmd(
    ctx: typer.Context,
    profile: Annotated[
        str, typer.Argument(help="Profile name from profiles.toml.")
    ] = "default",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Print the sbatch command without submitting.",
        ),
    ] = False,
) -> None:
    _require_sol()
    extra = list(ctx.args)
    code = sol_cmds.session_start(
        profile_name=profile,
        dry_run=dry_run,
        passthrough=extra,
    )
    raise typer.Exit(code=code)


@session_app.command("info", help="Show the current session (from session.json).")
def session_info_cmd(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit JSON.")
    ] = False,
) -> None:
    _require_sol()
    code = sol_cmds.session_info(json_output=json_output)
    raise typer.Exit(code=code)


@session_app.command(
    "stop", help="Cancel the recorded session and clear session.json."
)
def session_stop_cmd() -> None:
    _require_sol()
    code = sol_cmds.session_stop()
    raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@config_app.command("init", help="Write a starter profiles.toml.")
def config_init_cmd(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing profiles.toml.")
    ] = False,
) -> None:
    _require_sol()
    code = sol_cmds.config_init(force=force)
    raise typer.Exit(code=code)


@config_app.command("show", help="Print resolved profiles (with [shared] merged).")
def config_show_cmd(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit JSON.")
    ] = False,
) -> None:
    _require_sol()
    code = sol_cmds.config_show(json_output=json_output)
    raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Laptop-side stubs (deferred to a future release)
#
# Each stub accepts arbitrary extra args via context_settings so a real
# invocation like `solx up gpu` lands on the deferral message instead of
# Click's "unexpected argument" usage error.
# ---------------------------------------------------------------------------

_STUB_CTX = {"allow_extra_args": True, "ignore_unknown_options": True}


@app.command("init", help="(deferred to laptop-side release)", context_settings=_STUB_CTX)
def init_cmd(ctx: typer.Context) -> None:  # noqa: ARG001
    _laptop_stub()


@app.command("up", help="(deferred to laptop-side release)", context_settings=_STUB_CTX)
def up_cmd(ctx: typer.Context) -> None:  # noqa: ARG001
    _laptop_stub()


@app.command("down", help="(deferred to laptop-side release)", context_settings=_STUB_CTX)
def down_cmd(ctx: typer.Context) -> None:  # noqa: ARG001
    _laptop_stub()


@app.command("forward", help="(deferred to laptop-side release)", context_settings=_STUB_CTX)
def forward_cmd(ctx: typer.Context) -> None:  # noqa: ARG001
    _laptop_stub()


@app.command(
    "info",
    help="(deferred to laptop-side release; see `solx session info`)",
    context_settings=_STUB_CTX,
)
def info_cmd(ctx: typer.Context) -> None:  # noqa: ARG001
    _laptop_stub()

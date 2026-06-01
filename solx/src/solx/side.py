"""Detect whether the current host is part of the Sol cluster.

`solx` is Sol-only. Each subcommand asks `require_sol()` to enforce the
guard — wrong-side invocations exit 2 with a clear redirect rather than
attempting to talk to a Slurm controller that isn't there.
"""
from __future__ import annotations

import socket
import subprocess
from typing import Literal

import typer

Side = Literal["sol", "not-sol"]

SOL_HOSTNAME_SUFFIX = ".sol.rc.asu.edu"

_NOT_SOL_MESSAGE = (
    "solx is Sol-only — SSH to a Sol login node first, then re-run.\n"
    "See: https://docs.rc.asu.edu/"
)


def detect(*, _runner=None) -> Side:
    """Return "sol" if the current host is on the Sol cluster, else "not-sol".

    Looks for any token ending in `.sol.rc.asu.edu` in `hostname -a` and
    `socket.getfqdn()`. Tests inject `_runner` to fake the command output
    without shelling out.
    """
    runner = _runner or _hostname_a
    return "sol" if _matches_sol(runner()) else "not-sol"


def current_node() -> str:
    """Best-effort short hostname for human-facing messages."""
    try:
        return socket.gethostname().split(".")[0]
    except OSError:
        return "unknown"


def require_sol() -> None:
    """Exit 2 with a redirect message if not on Sol. Used by every subcommand."""
    if detect() != "sol":
        typer.echo(_NOT_SOL_MESSAGE, err=True)
        raise typer.Exit(code=2)


def _matches_sol(text: str) -> bool:
    return any(tok.endswith(SOL_HOSTNAME_SUFFIX) for tok in text.split())


def _hostname_a() -> str:
    """Run `hostname -a` and return its output; fall back to FQDN on failure."""
    fqdn = ""
    try:
        fqdn = socket.getfqdn()
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["hostname", "-a"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return fqdn
    return f"{result.stdout or ''} {fqdn}"

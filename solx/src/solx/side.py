"""Detect whether the current host is part of the Sol cluster."""
from __future__ import annotations

import socket
import subprocess
from typing import Literal

Side = Literal["sol", "not-sol"]

SOL_HOSTNAME_SUFFIX = ".sol.rc.asu.edu"


def detect(*, _runner=None) -> Side:
    """Return "sol" if the current host is part of the Sol cluster, else "not-sol".

    Parses the output of `hostname -a` (and falls back to the FQDN) for any
    token ending in `.sol.rc.asu.edu`. Tests inject `_runner` to fake the
    command output without shelling out.
    """
    runner = _runner or _hostname_a
    return "sol" if _matches_sol(runner()) else "not-sol"


def current_node() -> str:
    """Best-effort short hostname for human-facing messages."""
    try:
        return socket.gethostname().split(".")[0]
    except OSError:
        return "unknown"


def _hostname_a() -> str:
    """Run `hostname -a` and return its output. Falls back to the FQDN."""
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


def _matches_sol(hostname_output: str) -> bool:
    return any(
        token.endswith(SOL_HOSTNAME_SUFFIX)
        for token in hostname_output.split()
    )

"""`solx init` — write a starter `config.toml`."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

from rich.prompt import Confirm

from solx import config as cfg
from solx.output import Out


def cmd_init(
    *,
    path: Path | None = None,
    force: bool = False,
    out: Out | None = None,
    confirm_fn: Callable[..., bool] | None = None,
) -> int:
    out = out or Out.auto()
    p = path or cfg.config_path()

    if p.exists() and not force:
        # Never block on the overwrite prompt in a non-interactive session.
        if not out.interactive:
            out.error(f"[red]error:[/] {p} already exists. pass -f to overwrite.")
            return 2
        ask = confirm_fn or Confirm.ask
        if not ask(f"{p} already exists. Overwrite?", default=False):
            out.status("[dim]aborted[/]")
            return 1

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.starter_config_text())
    # Mode 0600 — config may eventually contain user-specific paths or
    # mail-user etc.; keep it readable only by the owner.
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)

    out.status("[dim]edit it with `solx config edit`, then `solx job start`.[/]")
    out.emit(data={"wrote": str(p)}, human=lambda: f"[green]wrote[/] {p}")
    return 0

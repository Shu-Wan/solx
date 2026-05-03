"""`solx init` — write a starter `config.toml`."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.prompt import Confirm

from solx import config as cfg


def cmd_init(
    *,
    path: Path | None = None,
    force: bool = False,
    console: Console | None = None,
    confirm_fn: Callable[..., bool] | None = None,
) -> int:
    console = console or Console()
    p = path or cfg.config_path()

    if p.exists() and not force:
        ask = confirm_fn or Confirm.ask
        if not ask(f"{p} already exists. Overwrite?", default=False):
            console.print("[dim]aborted[/]")
            return 1

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.starter_config_text())
    # Mode 0600 — config may eventually contain user-specific paths or
    # mail-user etc.; keep it readable only by the owner.
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)

    console.print(f"[green]wrote[/] {p}")
    console.print(
        "[dim]edit it with `solx config edit`, then `solx job start`.[/]"
    )
    return 0

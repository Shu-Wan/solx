"""`solx init` — write a starter `config.toml`."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

from rich.prompt import Confirm, Prompt

from solx import config as cfg
from solx.output import Out


SHELLS = ("bash", "zsh", "fish")


def _default_walkthrough(out: Out) -> str | None:
    """Interactive first-run walkthrough. Returns the chosen shell, or None.

    One question for now — which login shell `solx job jump` should open on the
    compute node. More steps can be added here later.
    """
    if not Confirm.ask("Walk through a quick setup?", default=False):
        return None
    return Prompt.ask(
        "Which shell should `solx job jump` open on the compute node?",
        choices=list(SHELLS),
        default="bash",
    )


def cmd_init(
    *,
    path: Path | None = None,
    force: bool = False,
    solkeep: Path | None = None,
    out: Out | None = None,
    confirm_fn: Callable[..., bool] | None = None,
    walkthrough_fn: Callable[[Out], str | None] | None = None,
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

    # If an existing ~/.solkeep is around (skill users), carry it into [keep].
    imported = cfg.import_solkeep(solkeep) if solkeep is not None else None

    # Optional interactive walkthrough — skipped entirely in a non-interactive
    # session (an agent/cron just gets the defaults, never a hung prompt).
    default_shell = "bash"
    if out.interactive:
        chosen = (walkthrough_fn or _default_walkthrough)(out)
        if chosen:
            default_shell = chosen

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.starter_config_text(keep=imported, default_shell=default_shell))
    # Mode 0600 — config may eventually contain user-specific paths or
    # mail-user etc.; keep it readable only by the owner.
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)

    if imported is not None:
        inc, exc = imported
        out.status(
            f"[green]imported[/] {len(inc)} include / {len(exc)} exclude "
            f"pattern(s) from {solkeep} into [keep]"
        )
    out.status("[dim]edit it with `solx config edit`, then `solx job start`.[/]")
    out.emit(data={"wrote": str(p)}, human=lambda: f"[green]wrote[/] {p}")
    return 0

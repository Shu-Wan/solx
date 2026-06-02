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


def _default_walkthrough(out: Out, solkeep: Path | None) -> dict | None:
    """Interactive first-run walkthrough. Returns answers, or None if declined.

    Steps (more can be added later): optionally import an existing `~/.solkeep`
    into `[keep]`, then pick the login shell `solx job jump` opens. Returns
    ``{"shell": str, "keep": (include, exclude) | None}``.
    """
    if not Confirm.ask("Walk through a quick setup?", default=False):
        return None

    keep = None
    if solkeep is not None:
        candidate = cfg.import_solkeep(solkeep)
        if candidate is not None:
            inc, exc = candidate
            if Confirm.ask(
                f"Found {solkeep} ({len(inc)} include / {len(exc)} exclude "
                "pattern(s)). Import it into \\[keep]?",  # \\[ escapes Rich markup
                default=True,
            ):
                keep = candidate

    shell = Prompt.ask(
        "Which shell should `solx job jump` open on the compute node?",
        choices=list(SHELLS),
        default="bash",
    )
    return {"shell": shell, "keep": keep}


def cmd_init(
    *,
    path: Path | None = None,
    force: bool = False,
    solkeep: Path | None = None,
    out: Out | None = None,
    confirm_fn: Callable[..., bool] | None = None,
    walkthrough_fn: Callable[[Out, Path | None], dict | None] | None = None,
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

    # Optional interactive walkthrough — skipped entirely in a non-interactive
    # session (an agent/cron just gets the defaults, never a hung prompt). The
    # `~/.solkeep` import is one of its prompted steps; importing is convenience
    # only — `solx keep` reads `~/.solkeep` at runtime regardless.
    imported = None
    default_shell = "bash"
    if out.interactive:
        result = (walkthrough_fn or _default_walkthrough)(out, solkeep)
        if result:
            default_shell = result.get("shell") or "bash"
            imported = result.get("keep")

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.starter_config_text(keep=imported, default_shell=default_shell))
    # Mode 0600 — config may eventually contain user-specific paths or
    # mail-user etc.; keep it readable only by the owner.
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)

    if imported is not None:
        inc, exc = imported
        out.status(
            f"[green]imported[/] {len(inc)} include / {len(exc)} exclude "
            "pattern(s) into \\[keep]"
        )
    out.status("[dim]edit it with `solx config edit`, then `solx job start`.[/]")
    out.emit(data={"wrote": str(p)}, human=lambda: f"[green]wrote[/] {p}")
    return 0

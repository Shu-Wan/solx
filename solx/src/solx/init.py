"""`solx init` — write a starter `config.toml`."""
from __future__ import annotations

import os
import stat
import tomllib
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

    # Step 1 — shell (a real choice, so the walkthrough doesn't open with two
    # yes/no questions in a row).
    out.status("\n[bold]Step 1 — shell[/]")
    shell = Prompt.ask(
        "Which shell should `solx job jump` open on the compute node?",
        choices=list(SHELLS),
        default="bash",
    )

    # Step 2 — scratch keep-list (only when there's a ~/.solkeep to offer).
    keep = None
    candidate = cfg.import_solkeep(solkeep) if solkeep is not None else None
    if candidate is not None:
        inc, exc = candidate
        out.status(
            f"\n[bold]Step 2 — scratch keep-list[/]  "
            f"({solkeep}: {len(inc)} include / {len(exc)} exclude)"
        )
        if Confirm.ask("Import it into \\[keep]?", default=True):  # \\[ escapes markup
            keep = candidate

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


def cmd_import_solkeep(
    *,
    path: Path | None = None,
    solkeep: Path | None = None,
    out: Out | None = None,
) -> int:
    """Migrate a legacy `~/.solkeep` keep-list into the config's `[keep]` block.

    The implicit `~/.solkeep` fallback (and the `.solkeep` format) is
    deprecated and loses support in a future release (see
    `keep.SOLKEEP_REMOVED_IN`); this is the one-shot migration. Reads `solkeep`
    (default `~/.solkeep`), splits it into include/exclude via `import_solkeep`,
    and appends a rendered `[keep]` block to an existing `config.toml`. The
    merged document is validated before anything is written, so a pattern that
    can't round-trip through TOML never leaves a corrupt config on disk.
    Refuses if the config already has an active `[keep]` table — a second one
    is invalid TOML, so the user must merge by hand there. `.solkeep` is
    gitignore last-match-wins while `[keep]` is include-minus-exclude, so an
    order-dependent re-include can't be preserved; the command warns when it
    sees one (`solkeep_is_order_sensitive`).
    """
    out = out or Out.auto()
    p = path or cfg.config_path()
    src = solkeep or (Path.home() / ".solkeep")

    if not p.exists():
        out.error(
            f"[red]error:[/] no config at {p}. run `solx init` first, then re-run this."
        )
        return 2

    imported = cfg.import_solkeep(src)
    if imported is None:
        out.error(
            f"[red]error:[/] nothing to import from {src} (missing or no patterns)."
        )
        return 2
    include, exclude = imported

    try:
        existing = cfg.load(p)
    except cfg.ConfigError as e:
        out.error(f"[red]error:[/] {e}")
        return 2
    if existing.keep is not None:
        out.error(
            r"[red]error:[/] config already has a \[keep] block. merge the "
            "patterns by hand with `solx config edit` (a second \\[keep] table "
            "would be invalid TOML)."
        )
        return 2

    block = cfg.render_keep_block(include, exclude, source=str(src))
    # Validate the merged document before touching the file: a pattern that
    # can't round-trip through TOML must never leave a corrupt config on disk.
    new_text = p.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + block
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        out.error(
            f"[red]error:[/] importing these patterns would produce invalid TOML "
            f"({e}); config left unchanged. Fix {src} or run `solx config edit`."
        )
        return 1
    p.write_text(new_text, encoding="utf-8")

    out.status(
        f"[green]imported[/] {len(include)} include / {len(exclude)} exclude "
        r"pattern(s) into \[keep]"
    )
    if cfg.solkeep_is_order_sensitive(src):
        out.status(
            r"[yellow]warning:[/] this keep-list re-includes a path under an "
            r"earlier `!` carve-out. A \[keep] block (include minus exclude) "
            "can't preserve that ordering, so it may now renew fewer "
            f"directories. Compare `solx keep --dry-run` against {src} and keep "
            "the old file until you've confirmed the result matches."
        )
    else:
        out.status(
            "[dim]review with `solx config show`, then verify with "
            "`solx keep --dry-run` before removing the old keep-list.[/]"
        )
    out.emit(
        data={"config": str(p), "include": include, "exclude": exclude},
        human=lambda: f"[green]wrote[/] \\[keep] → {p}",
    )
    return 0

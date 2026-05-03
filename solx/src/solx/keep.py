"""`solx keep` — port of `sol_renew.py`'s mechanism with `[keep]` config.

Read Sol's warning CSVs from `--csv-dir`, intersect flagged directories
with `[keep]` include/exclude (via `pathspec`), and `touch -a -m -c` only
the intersection. Preserves the original tool's "only renew what Sol has
explicitly flagged" ethical posture — we don't walk `/scratch` wholesale.
"""
from __future__ import annotations

import csv
import shlex
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.prompt import Confirm

from solx.config import Config, KeepRules


STAGE_FILES = {
    "pending": "scratch-dirs-pending-removal.csv",
    "over90": "scratch-dirs-over-90days.csv",
    "inactive": "scratch-dirs-inactive.csv",
}
STAGE_ORDER = ("pending", "over90", "inactive")
STAGES_ALL = "all"


@dataclass(frozen=True)
class Plan:
    """The directories `solx keep` would touch (`kept`) vs filter out (`skipped`)."""

    kept: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.kept and not self.skipped


# --- planning -------------------------------------------------------------


def load_csv_dirs(csv_path: Path) -> list[str]:
    """Return the `Directory` column from one of Sol's warning CSVs.

    Missing file is fine — Sol only drops the CSV when there's something to
    flag. Empty result means nothing to do for that stage.
    """
    if not csv_path.exists():
        return []
    dirs: list[str] = []
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            d = (row.get("Directory") or "").strip()
            if d:
                dirs.append(d)
    return dirs


def build_plan(
    csv_dir: Path,
    stages: list[str],
    keep: KeepRules,
) -> Plan:
    """Walk the chosen stages' CSVs and split flagged dirs into kept/skipped."""
    kept: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for stage in stages:
        for d in load_csv_dirs(csv_dir / STAGE_FILES[stage]):
            if d in seen:
                continue
            seen.add(d)
            (kept if keep.matches(d) else skipped).append((stage, d))
    return Plan(kept=kept, skipped=skipped)


# --- touching -------------------------------------------------------------


def touch_dir(directory: str) -> tuple[str, int, int, str]:
    """Touch atime+mtime on every file under `directory`.

    Returns (directory, files_touched, errors, message). Uses one find walk
    with `-fprint0` + xargs to avoid double-walking on slow NFS, mirroring
    `sol_renew.py`.
    """
    import os

    if not os.path.isdir(directory):
        return (directory, 0, 0, "skipped: not a directory")

    q = shlex.quote(directory)
    cmd = (
        'tmp=$(mktemp) || exit 99; '
        'trap \'rm -f "$tmp"\' EXIT; '
        f"find {q} -type f -fprint0 \"$tmp\"; "
        "count=$(tr -cd '\\0' <\"$tmp\" | wc -c); "
        "xargs -0 -r -n 500 -a \"$tmp\" touch -a -m -c --; "
        'rc=$?; '
        'printf "COUNT:%s\\n" "$count"; exit $rc'
    )
    res = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, check=False
    )
    count = 0
    for line in res.stdout.splitlines():
        if line.startswith("COUNT:"):
            try:
                count = int(line.split(":", 1)[1])
            except ValueError:
                pass
    if res.returncode == 0:
        return (directory, count, 0, "ok")
    return (directory, count, 1, f"exit {res.returncode}: {res.stderr.strip()}")


# --- command --------------------------------------------------------------


def cmd_keep(
    *,
    config: Config,
    csv_dir: Path | None,
    stage: str,
    jobs_n: int,
    yes: bool,
    dry_run: bool,
    verbose: bool,
    console: Console | None = None,
    confirm_fn: Callable[..., bool] | None = None,
    touch_fn: Callable[[str], tuple[str, int, int, str]] | None = None,
) -> int:
    console = console or Console()

    if yes and dry_run:
        console.print("[red]error:[/] --yes and --dry-run are mutually exclusive")
        return 2

    if config.keep is None:
        console.print(
            r"[red]error:[/] no \[keep] block in config. "
            "run `solx config edit` to add one."
        )
        return 2

    csv_dir = csv_dir or Path.home()
    stages = list(STAGE_ORDER) if stage == STAGES_ALL else [stage]

    plan = build_plan(csv_dir, stages, config.keep)
    _print_plan_summary(console, plan, csv_dir, stages, verbose)

    if not plan.kept:
        console.print(
            "[dim]no flagged directories matched [keep] — nothing to do.[/]"
        )
        return 0

    if dry_run:
        return 0

    if not yes:
        ask = confirm_fn or Confirm.ask
        if not ask(
            f"Touch mtimes on {len(plan.kept)} directories?", default=False
        ):
            console.print("[dim]aborted[/]")
            return 1

    return _execute(plan, jobs_n, console, touch_fn)


def _print_plan_summary(
    console: Console,
    plan: Plan,
    csv_dir: Path,
    stages: list[str],
    verbose: bool,
) -> None:
    console.print(
        f"[dim]csv-dir:[/] {csv_dir}  "
        f"[dim]stages:[/] {', '.join(stages)}"
    )
    console.print(
        f"[bold]plan:[/] {len(plan.kept)} kept, {len(plan.skipped)} skipped"
    )
    if verbose:
        if plan.kept:
            console.print("[green]kept:[/]")
            for stage_name, d in plan.kept[:20]:
                console.print(f"  [dim]{stage_name:>9}[/] {d}")
            if len(plan.kept) > 20:
                console.print(f"  [dim]… and {len(plan.kept) - 20} more[/]")
        if plan.skipped:
            console.print(
                r"[yellow]skipped[/] (flagged by Sol but not in \[keep]):"
            )
            for stage_name, d in plan.skipped[:20]:
                console.print(f"  [dim]{stage_name:>9}[/] {d}")


def _execute(
    plan: Plan,
    jobs_n: int,
    console: Console,
    touch_fn: Callable[[str], tuple[str, int, int, str]] | None,
) -> int:
    """Run `touch_fn` over `plan.kept` directories with `jobs_n` workers."""
    fn = touch_fn or touch_dir
    ok = 0
    failed = 0
    total_files = 0
    if jobs_n <= 1:
        # Serial path — also used in tests so we don't spawn processes.
        for _, d in plan.kept:
            _, n, errs, msg = fn(d)
            total_files += n
            if errs:
                failed += 1
                console.print(f"[red]error[/] {d}: {msg}")
            else:
                ok += 1
    else:
        with ProcessPoolExecutor(max_workers=jobs_n) as pool:
            futs = {pool.submit(fn, d): d for _, d in plan.kept}
            for fut in as_completed(futs):
                d, n, errs, msg = fut.result()
                total_files += n
                if errs:
                    failed += 1
                    console.print(f"[red]error[/] {d}: {msg}")
                else:
                    ok += 1
    console.print(
        f"[green]done[/] {ok}/{len(plan.kept)} dirs · "
        f"{total_files} files touched"
        + (f" · [red]{failed} failed[/]" if failed else "")
    )
    return 1 if failed else 0

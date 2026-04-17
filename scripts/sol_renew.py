#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13.0"]
# ///
"""
Renew scratch files on ASU Sol before Sol's layered deletion pipeline
removes them. Reads the per-stage CSV warnings Sol drops in $HOME,
intersects them with $HOME/.solignore (gitignore-style patterns that
mark directories to KEEP), and runs `touch -a -m -c` only on files
inside the flagged directories. The script walks each flagged
directory once per run; the scope of work is bounded by Sol's CSVs and
your .solignore -- it does not start from /scratch and recurse. If you
keep-list a very large subtree, the touch pass will still be large.

ASU Research Computing defines the deletion policy (thresholds, CSV
filenames, cadence); their official doc is authoritative:
https://docs.rc.asu.edu/scratch

Usage:
    sol_renew.py                          # default: all non-removed stages
    sol_renew.py --stage pending          # only the most urgent CSV
    sol_renew.py --dry-run                # show planned work, touch nothing
    sol_renew.py --jobs 16 -v             # 16 worker processes, verbose

Stages (CSVs Sol writes into $HOME at time of writing):
    pending   -> scratch-dirs-pending-removal.csv  (most urgent)
    over90    -> scratch-dirs-over-90days.csv
    inactive  -> scratch-dirs-inactive.csv         (earliest warning)
    all       -> pending + over90 + inactive (default)

.solignore syntax (gitignore-like, but semantics are INVERTED -- matched
paths are KEPT, not ignored). Rules are matched against the Directory
column of Sol's CSVs -- i.e. directory paths -- not individual files.
Patterns are literal; no shell expansion.

    # comments and blank lines allowed
    /scratch/sparky/project         # bare path = everything under that dir
    /scratch/sparky/runs/*          # glob on directory names
    /scratch/sparky/data/**         # ** for recursive match
    !/scratch/sparky/data/tmp/**    # ! to exclude a sub-tree
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

STAGE_FILES = {
    "pending": "scratch-dirs-pending-removal.csv",
    "over90": "scratch-dirs-over-90days.csv",
    "inactive": "scratch-dirs-inactive.csv",
}
STAGE_ORDER = ("pending", "over90", "inactive")


# ---------- .solignore matcher (gitignore-style, stdlib only) ----------

@dataclass
class Rule:
    raw: str
    negate: bool
    regex: "re.Pattern[str]"


def _glob_to_regex(pat: str) -> str:
    i, n = 0, len(pat)
    out: list[str] = []
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                if i + 2 < n and pat[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _compile_rule(raw: str) -> Rule | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None

    negate = line.startswith("!")
    if negate:
        line = line[1:]

    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]

    anchored = line.startswith("/")
    if anchored:
        line = line[1:]

    has_meta = any(ch in line for ch in "*?[")
    # Bare path (no glob chars) = directory prefix: matches path and everything under
    suffix = "" if has_meta or dir_only else r"(?:/.*)?"
    body = _glob_to_regex(line) + suffix

    full = ("^/" + body + "$") if anchored else ("(?:^|/)" + body + "$")
    return Rule(raw=raw.rstrip("\n"), negate=negate, regex=re.compile(full))


def load_solignore(path: Path) -> list[Rule]:
    if not path.exists():
        return []
    rules: list[Rule] = []
    for raw in path.read_text().splitlines():
        rule = _compile_rule(raw)
        if rule is not None:
            rules.append(rule)
    return rules


def matches(path: str, rules: list[Rule]) -> bool:
    """Last matching rule wins. A match means KEEP."""
    kept = False
    for r in rules:
        if r.regex.search(path):
            kept = not r.negate
    return kept


# ---------- CSV loading ----------

def load_csv_dirs(csv_path: Path) -> list[str]:
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


# ---------- touching ----------

def touch_dir(directory: str) -> tuple[str, int, int, str]:
    """Touch every file under `directory` in a single NFS walk.

    Returns (directory, files_touched, errors, message).
    """
    if not os.path.isdir(directory):
        return (directory, 0, 0, "skipped: not a directory")

    q = shlex.quote(directory)
    # -fprint0 walks once, writes NUL-separated paths to a tmp file. We then
    # count NULs and feed the same file to xargs. No double walk.
    # Deliberately not using `set -e`: we need the tmp cleanup + COUNT line to
    # run even if xargs returns non-zero from partial touch failures.
    cmd = (
        'tmp=$(mktemp) || exit 99; '
        'trap \'rm -f "$tmp"\' EXIT; '
        f"find {q} -type f -fprint0 \"$tmp\"; "
        "count=$(tr -cd '\\0' <\"$tmp\" | wc -c); "
        "xargs -0 -r -n 500 -a \"$tmp\" touch -a -m -c --; "
        'rc=$?; '
        'printf "COUNT:%s\\n" "$count"; exit $rc'
    )
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        return (directory, 0, 1, f"exec failed: {e}")

    touched = 0
    for line in proc.stdout.splitlines():
        if line.startswith("COUNT:"):
            try:
                touched = int(line.split(":", 1)[1])
            except ValueError:
                pass

    if proc.returncode != 0:
        err = (
            proc.stderr.strip().splitlines()[-1]
            if proc.stderr.strip()
            else "nonzero exit"
        )
        return (directory, touched, 1, err)
    return (directory, touched, 0, "ok")


# ---------- planning ----------

@dataclass
class Plan:
    kept: list[tuple[str, str]]
    skipped: list[tuple[str, str]]


def build_plan(csv_dir: Path, stages: list[str], rules: list[Rule]) -> Plan:
    seen: set[str] = set()
    kept: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for stage in stages:
        for d in load_csv_dirs(csv_dir / STAGE_FILES[stage]):
            if d in seen:
                continue
            seen.add(d)
            (kept if matches(d, rules) else skipped).append((stage, d))
    return Plan(kept=kept, skipped=skipped)


# ---------- CLI ----------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Surgically renew scratch files flagged by Sol.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--stage",
        choices=["pending", "over90", "inactive", "all"],
        default="all",
    )
    ap.add_argument("--csv-dir", default=os.path.expanduser("~"))
    ap.add_argument("--solignore", default=os.path.expanduser("~/.solignore"))
    ap.add_argument(
        "--jobs", "-j", type=int,
        # NFS is the bottleneck; too many concurrent walkers hurt more than
        # they help. Cap the default low, let users raise it explicitly.
        default=min(8, max(1, (os.cpu_count() or 2) // 4)),
    )
    ap.add_argument("--dry-run", "-n", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    console = Console()
    is_tty = console.is_terminal

    rules = load_solignore(Path(args.solignore))
    if not rules:
        console.print(
            f"[yellow]warning:[/] no rules loaded from {args.solignore}",
            style="yellow",
        )
        console.print(
            "nothing will be touched. add patterns to protect files.",
            style="dim",
        )
        return 2

    stages = list(STAGE_ORDER) if args.stage == "all" else [args.stage]
    plan = build_plan(Path(args.csv_dir), stages, rules)

    _print_plan_summary(console, args, rules, stages, plan)

    if not plan.kept:
        console.print("no matching directories; nothing to do.", style="dim")
        return 0
    if args.dry_run:
        console.print("\n[cyan]dry-run:[/] no files touched.")
        return 0

    console.print(
        f"\n[bold]touching[/] files in [cyan]{len(plan.kept)}[/] directories "
        f"with [cyan]{args.jobs}[/] workers..."
    )
    console.print(
        "[dim]NFS touch on a directory with many files can take minutes "
        "with no per-file output. do not cancel early.[/]"
    )

    start = time.time()
    ok = fail = total_files = 0

    if is_tty:
        ok, fail, total_files = _run_with_progress(console, plan, args.jobs)
    else:
        ok, fail, total_files = _run_plain(console, plan, args.jobs)

    elapsed = time.time() - start
    _print_final_summary(console, ok, fail, total_files, elapsed)
    return 0 if fail == 0 else 1


# ---------- output helpers ----------

_PREVIEW = 5  # cap for verbose per-stage listing


def _shorten(path: str, width: int) -> str:
    if len(path) <= width:
        return path
    head = width // 3
    tail = width - head - 1
    return path[:head] + "…" + path[-tail:]


def _print_plan_summary(console, args, rules, stages, plan) -> None:
    by_stage_kept: dict[str, list[str]] = {s: [] for s in stages}
    by_stage_skip: dict[str, list[str]] = {s: [] for s in stages}
    for s, d in plan.kept:
        by_stage_kept[s].append(d)
    for s, d in plan.skipped:
        by_stage_skip[s].append(d)

    table = Table(title="Sol renewal plan", title_style="bold", box=None)
    table.add_column("stage", style="cyan")
    table.add_column("keep", justify="right", style="green")
    table.add_column("skip", justify="right", style="dim")
    for s in stages:
        table.add_row(s, str(len(by_stage_kept[s])), str(len(by_stage_skip[s])))
    table.add_section()
    table.add_row(
        "[bold]total",
        f"[bold green]{len(plan.kept)}",
        f"[bold dim]{len(plan.skipped)}",
    )
    console.print()
    console.print(f"rules from [cyan]{args.solignore}[/]: {len(rules)}")
    console.print(table)

    if args.verbose:
        for s in stages:
            _preview_bucket(console, s, "keep", by_stage_kept[s], style="green")
            _preview_bucket(console, s, "skip", by_stage_skip[s], style="dim")


def _preview_bucket(console, stage, label, items, style) -> None:
    if not items:
        return
    console.print(f"  [{style}]{stage:8s} {label}[/] ({len(items)}):")
    for d in items[:_PREVIEW]:
        console.print(f"    {d}", style=style)
    if len(items) > _PREVIEW:
        console.print(
            f"    [dim]... and {len(items) - _PREVIEW} more[/]"
        )


def _run_with_progress(console, plan, jobs):
    ok = fail = total_files = 0
    total = len(plan.kept)
    width = max(40, console.width - 60)

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("ok={task.fields[ok]} fail={task.fields[fail]}"),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    task_id = progress.add_task(
        "renewing", total=total, ok=0, fail=0
    )

    with progress, ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(touch_dir, d): d for _, d in plan.kept}
        for fut in as_completed(futs):
            d = futs[fut]
            _, touched, errors, msg = fut.result()
            total_files += touched
            if errors:
                fail += 1
                progress.console.print(
                    f"[red]FAIL[/] {_shorten(d, width)} :: {msg}"
                )
            else:
                ok += 1
            progress.update(task_id, advance=1, ok=ok, fail=fail,
                            description=f"renewing [dim]{_shorten(d, width)}[/]")
    return ok, fail, total_files


def _run_plain(console, plan, jobs):
    """Non-TTY output: one concise line per completed directory."""
    ok = fail = total_files = 0
    total = len(plan.kept)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(touch_dir, d): d for _, d in plan.kept}
        for i, fut in enumerate(as_completed(futs), 1):
            d = futs[fut]
            _, touched, errors, msg = fut.result()
            total_files += touched
            if errors:
                fail += 1
                console.print(f"[{i}/{total}] FAIL {d} :: {msg}")
            else:
                ok += 1
                console.print(f"[{i}/{total}] ok   {touched:>7d} files  {d}")
    return ok, fail, total_files


def _print_final_summary(console, ok, fail, total_files, elapsed) -> None:
    table = Table(box=None, show_header=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("dirs ok", f"[green]{ok}")
    table.add_row("dirs failed", f"[red]{fail}" if fail else "0")
    table.add_row("files touched", f"{total_files:,}")
    table.add_row("elapsed", f"{elapsed:.1f}s")
    console.print()
    console.print(table)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["rich>=13.0"]
# ///
"""
Renew scratch files on ASU Sol before Sol's layered deletion pipeline
removes them. Reads the per-stage CSV warnings Sol drops in $HOME,
intersects them with $HOME/.solkeep (gitignore-style patterns that
mark directories to KEEP), and runs `touch -a -m -c` only on files
inside the flagged directories. The script walks each flagged
directory once per run; the scope of work is bounded by Sol's CSVs and
your .solkeep -- it does not start from /scratch and recurse. If you
keep-list a very large subtree, the touch pass will still be large.

Work is sharded at the FILE level, not the directory level: the run
first enumerates every kept directory in parallel, then touches the
resulting files in evenly-sized batches across the worker pool. A
single huge directory therefore uses the whole pool instead of pinning
one worker -- `-j` scales the slowest single directory, not just the
count of directories. This is metadata-heavy I/O: on Sol, run it on a
compute node or the DTN (`ssh soldtn`), not a throttled login node
(see SKILL.md).

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

.solkeep syntax (gitignore-like, but semantics are INVERTED -- matched
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

# Files per touch shard in the parallel touch phase. Big enough that the
# per-batch subprocess overhead is negligible, small enough that one huge
# directory fans out into many batches and keeps every worker busy. xargs
# re-splits each batch into `touch` calls of 500 internally.
BATCH = 2000


# ---------- .solkeep matcher (gitignore-style, stdlib only) ----------

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


def load_solkeep(path: Path) -> list[Rule]:
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
#
# Two phases, both run across the worker pool:
#   1. enumerate_dir -- one `find` walk per kept directory, returns its files
#   2. touch_files   -- `touch -a -m -c` a batch of those files
# Phase 2 is the expensive half (one metadata write per file); sharding it at
# the file level is what lets a single 50k-file directory use the whole pool
# instead of pinning one worker. Paths are kept as bytes end-to-end so a
# non-UTF-8 filename can't crash the run.


def enumerate_dir(directory: str) -> tuple[str, list[bytes], str]:
    """List every regular file under `directory` in one NFS walk.

    Returns (directory, file_paths, message). A path that isn't a directory
    (e.g. flagged then removed) is reported as a benign skip, not an error,
    mirroring the old per-directory behaviour.
    """
    if not os.path.isdir(directory):
        return (directory, [], "skipped: not a directory")

    q = shlex.quote(directory)
    try:
        proc = subprocess.run(
            ["bash", "-c", f"find {q} -type f -print0"],
            capture_output=True,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        return (directory, [], f"exec failed: {e}")

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return (directory, [], err[-1] if err else "find: nonzero exit")
    files = [p for p in proc.stdout.split(b"\0") if p]
    return (directory, files, "ok")


def touch_files(paths: list[bytes]) -> tuple[int, int, str]:
    """`touch -a -m -c` a batch of files in one xargs pass.

    Returns (files_attempted, errors, message). `touch -c` never creates a
    file and exits 0 on a path that no longer exists, so a file deleted
    between enumeration and touch is silently skipped, not an error. A
    nonzero exit means a real failure (e.g. permission, I/O), which we
    surface.
    """
    if not paths:
        return (0, 0, "ok")

    data = b"\0".join(paths) + b"\0"
    try:
        proc = subprocess.run(
            ["xargs", "-0", "-r", "-n", "500", "touch", "-a", "-m", "-c", "--"],
            input=data,
            capture_output=True,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        return (len(paths), 1, f"exec failed: {e}")

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return (len(paths), 1, err[-1] if err else "touch: nonzero exit")
    return (len(paths), 0, "ok")


def shard(files: list[bytes], batch_size: int = BATCH) -> list[list[bytes]]:
    """Split a flat file list into evenly-sized batches for the touch pool."""
    return [files[i : i + batch_size] for i in range(0, len(files), batch_size)]


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
    ap.add_argument("--solkeep", default=os.path.expanduser("~/.solkeep"))
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

    rules = load_solkeep(Path(args.solkeep))
    if not rules:
        console.print(
            f"[yellow]warning:[/] no rules loaded from {args.solkeep}",
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

    start = time.time()

    # Phase 1: enumerate every kept directory in parallel.
    console.print(
        f"\n[bold]enumerating[/] [cyan]{len(plan.kept)}[/] directories "
        f"with [cyan]{args.jobs}[/] workers..."
    )
    work, n_files, enum_fail = _enumerate(console, plan, args.jobs)

    # Phase 2: touch the enumerated files, sharded into batches across the pool
    # so one huge directory doesn't pin a single worker.
    console.print(
        f"[bold]touching[/] [cyan]{n_files:,}[/] files in "
        f"[cyan]{len(work)}[/] batches across [cyan]{args.jobs}[/] workers..."
    )
    console.print(
        "[dim]a large touch pass can take minutes with no per-file output. "
        "do not cancel early.[/]"
    )

    if is_tty:
        fail, total_files = _run_with_progress(console, work, args.jobs)
    else:
        fail, total_files = _run_plain(console, work, args.jobs)
    fail += enum_fail

    elapsed = time.time() - start
    _print_final_summary(console, len(plan.kept), total_files, fail, elapsed)
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
    console.print(f"rules from [cyan]{args.solkeep}[/]: {len(rules)}")
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


def _enumerate(console, plan, jobs):
    """Phase 1: walk every kept dir in parallel into file batches.

    Returns (work, n_files, enum_failures) where `work` is a list of
    (directory, batch) pairs -- each directory's files split into shards but
    tagged with their source dir so progress output stays recognizable.
    """
    dirs = [d for _, d in plan.kept]
    work: list[tuple[str, list[bytes]]] = []
    n_files = 0
    fail = 0
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for directory, files, msg in pool.map(enumerate_dir, dirs):
            if msg.startswith("skipped"):
                continue  # flagged but no longer a directory -- benign
            if msg != "ok":
                fail += 1
                console.print(f"[red]FAIL[/] enumerate {directory} :: {msg}")
                continue
            n_files += len(files)
            for batch in shard(files):
                work.append((directory, batch))
    return work, n_files, fail


def _run_with_progress(console, work, jobs):
    fail = total_files = 0
    width = max(40, console.width - 60)

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("files={task.fields[files]} fail={task.fields[fail]}"),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    task_id = progress.add_task("touching", total=len(work), files=0, fail=0)

    with progress, ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(touch_files, batch): d for d, batch in work}
        for fut in as_completed(futs):
            d = futs[fut]
            touched, errors, msg = fut.result()
            total_files += touched
            if errors:
                fail += 1
                progress.console.print(
                    f"[red]FAIL[/] {_shorten(d, width)} :: {msg}"
                )
            progress.update(task_id, advance=1, files=total_files, fail=fail,
                            description=f"touching [dim]{_shorten(d, width)}[/]")
    return fail, total_files


def _run_plain(console, work, jobs):
    """Non-TTY output: one concise line per completed file batch."""
    fail = total_files = 0
    total = len(work)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(touch_files, batch): d for d, batch in work}
        for i, fut in enumerate(as_completed(futs), 1):
            d = futs[fut]
            touched, errors, msg = fut.result()
            total_files += touched
            if errors:
                fail += 1
                console.print(f"[{i}/{total}] FAIL {d} :: {msg}")
            else:
                console.print(f"[{i}/{total}] ok   {touched:>7d} files  {d}")
    return fail, total_files


def _print_final_summary(console, n_dirs, total_files, fail, elapsed) -> None:
    table = Table(box=None, show_header=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("dirs", f"{n_dirs}")
    table.add_row("files touched", f"{total_files:,}")
    table.add_row("failures", f"[red]{fail}" if fail else "0")
    table.add_row("elapsed", f"{elapsed:.1f}s")
    console.print()
    console.print(table)


if __name__ == "__main__":
    sys.exit(main())

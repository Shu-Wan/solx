"""`solx keep` — renew scratch files Sol has flagged, filtered by `[keep]`.

Read Sol's warning CSVs from `--csv-dir`, intersect the flagged directories
with the `[keep]` include/exclude globs from config (via `pathspec`), and
`touch -a -m -c` only the intersection. Preserves the original tool's "only
renew what Sol has explicitly flagged" ethical posture — we never walk
`/scratch` wholesale.

Execution is file-level-sharded (PR #18): a bounded streaming pipeline over
one worker pool — enumerate a kept directory, split its files into evenly-sized
batches, and `touch` the batches across the pool. A single huge directory
fans out into many batches, so `-j` scales the parallelism of the whole run
including its largest directory, not just the count of directories.
Enumeration uses `fd` (or `rg`) when on `PATH` — both walk a tree
multithreaded — and `find` otherwise.

This is metadata-heavy NFS I/O. On Sol run it on a compute node or the DTN
(`ssh soldtn`), not a throttled login node.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from solx.config import Config, KeepRules, load_solkeep
from solx.output import Out


STAGE_FILES = {
    "pending": "scratch-dirs-pending-removal.csv",
    "over90": "scratch-dirs-over-90days.csv",
    "inactive": "scratch-dirs-inactive.csv",
}
STAGE_ORDER = ("pending", "over90", "inactive")
STAGES_ALL = "all"

# ~/.solkeep is the legacy keep-list the standalone sol_renew.py used. solx keep
# still reads it as a last-resort fallback, but the config [keep] block is the
# supported home now; the implicit fallback and the .solkeep format lose support
# in this release line.
SOLKEEP_REMOVED_IN = "1.0.0"

# Files per touch shard. Big enough that per-batch subprocess overhead is
# negligible, small enough that one huge directory fans out into many batches
# and keeps every worker busy. xargs re-splits each batch into `touch` calls
# of 500 internally.
BATCH = 2000

# Cap on how many dirs we inline into a JSON payload. Sol's warning CSVs can
# list thousands of flagged dirs; emitting them all makes a multi-megabyte
# document that blows an agent's context. We cap the inlined sample and always
# report the true totals + a `*_truncated` flag (agent-native principle #5:
# bounded responses). Counts are always exact; the lists are a sample.
JSON_LIST_CAP = 100


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

    A missing file is fine — Sol only drops the CSV when there's something to
    flag. An empty result means nothing to do for that stage.
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


def build_plan(csv_dir: Path, stages: list[str], keep: KeepRules) -> Plan:
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


# --- enumeration + touching ----------------------------------------------
#
# Two task kinds run on one worker pool:
#   enumerate_dir -- walk a kept directory, return its files
#   touch_files   -- `touch -a -m -c` a batch of those files
# touch is the expensive half (one metadata write per file), so it is sharded
# into file batches and spread across the pool. Paths are kept as bytes
# end-to-end so a non-UTF-8 filename can't crash the run.


def _pick_lister() -> tuple[str, str]:
    """Choose the fastest available file lister: (kind, binary path).

    `fd` and `rg` walk a directory tree multithreaded, faster than `find` on a
    large directory; `find` is the always-present fallback.

    The `--hidden --no-ignore` flags are LOAD-BEARING, not cosmetic: both fd
    and rg skip dotfiles and honor .gitignore/.fdignore/global-ignore by
    default, so without them a renewal would silently skip hidden and
    git-ignored files and under-protect them. With both flags, each matches
    `find -type f`. Detection is via `shutil.which`, so a shell alias/function
    named `rg` (e.g. Claude Code's bundled ripgrep shim) is ignored — only a
    real PATH binary is used.
    """
    for name in ("fd", "fdfind"):  # fdfind = the binary name on Debian/Ubuntu
        binary = shutil.which(name)
        if binary:
            return ("fd", binary)
    binary = shutil.which("rg")
    if binary:
        return ("rg", binary)
    return ("find", "find")


# Resolved once at import; ProcessPoolExecutor workers inherit it (fork) or
# recompute it cheaply (spawn).
LISTER_KIND, LISTER_BIN = _pick_lister()


def enumerate_dir(directory: str) -> tuple[str, list[bytes], str]:
    """List every regular file under `directory` in one walk.

    Returns (directory, file_paths, message). A path that isn't a directory
    (e.g. flagged then removed) is reported as a benign skip, not an error.
    """
    if not os.path.isdir(directory):
        return (directory, [], "skipped: not a directory")

    if LISTER_KIND == "fd":
        argv = [LISTER_BIN, "--hidden", "--no-ignore", "--type", "f",
                "--print0", "--search-path", directory]
    elif LISTER_KIND == "rg":
        argv = [LISTER_BIN, "--files", "--hidden", "--no-ignore", "--null",
                directory]
    else:
        argv = ["find", directory, "-type", "f", "-print0"]

    try:
        proc = subprocess.run(argv, capture_output=True, check=False)
    except Exception as e:  # noqa: BLE001
        return (directory, [], f"exec failed: {e}")

    # rg exits 1 when it lists no files -- that's an empty (but valid)
    # directory, not an error. fd/find return 0 in that case; for all three a
    # genuinely bad walk (permission, I/O) is rg>=2 / fd!=0 / find!=0.
    empty_ok = LISTER_KIND == "rg" and proc.returncode == 1 and not proc.stdout
    if proc.returncode != 0 and not empty_ok:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return (directory, [], err[-1] if err else f"{LISTER_KIND}: nonzero exit")
    files = [p for p in proc.stdout.split(b"\0") if p]
    return (directory, files, "ok")


def touch_files(paths: list[bytes]) -> tuple[int, int, str]:
    """`touch -a -m -c` a batch of files in one xargs pass.

    Returns (files_attempted, errors, message). `touch -c` never creates a
    file and exits 0 on a path that no longer exists, so a file deleted
    between enumeration and touch is silently skipped, not an error. A nonzero
    exit means a real failure (permission, I/O), which we surface.
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


# --- command --------------------------------------------------------------


def cmd_keep(
    *,
    config: Config | None,
    csv_dir: Path | None,
    stage: str,
    jobs_n: int,
    yes: bool,
    dry_run: bool,
    verbose: bool,
    solkeep: Path | None = None,
    out: Out | None = None,
    confirm_fn: Callable[..., bool] | None = None,
    execute_fn: Callable[..., tuple[int, int]] | None = None,
) -> int:
    out = out or Out.auto()

    if yes and dry_run:
        out.error("[red]error:[/] --yes and --dry-run are mutually exclusive")
        return 2

    # Keep-list source, in precedence order: explicit --solkeep > config
    # [keep] > the skill's ~/.solkeep (so an existing .solkeep just works).
    if solkeep is not None:
        keep_rules = load_solkeep(solkeep)
        if keep_rules is None:
            out.error(f"[red]error:[/] no keep rules found in {solkeep}")
            return 2
    elif config is not None and config.keep is not None:
        keep_rules = config.keep
    else:
        keep_rules = load_solkeep(Path.home() / ".solkeep")
        if keep_rules is None:
            out.error(
                r"[red]error:[/] no \[keep] block in config and no ~/.solkeep. "
                r"run `solx config edit` to add a \[keep] block."
            )
            return 2
        # The .solkeep fallback is deprecated — nudge migration into [keep].
        out.status(
            f"[yellow]deprecated:[/] reading the keep-list from ~/.solkeep is "
            f"deprecated and loses support in solx {SOLKEEP_REMOVED_IN}. "
            r"migrate it into your config's \[keep] block:  solx config import-solkeep"
        )

    csv_dir = csv_dir or Path.home()
    if not csv_dir.is_dir():
        out.error(
            f"[red]error:[/] --csv-dir {csv_dir} is not a directory "
            "(Sol drops the warning CSVs in $HOME)."
        )
        return 2
    stages = list(STAGE_ORDER) if stage == STAGES_ALL else [stage]

    plan = build_plan(csv_dir, stages, keep_rules)
    _report_plan(out, plan, csv_dir, stages, verbose)

    if not plan.kept:
        if out.json_mode:
            # Still emit a document so an agent gets structured output, not
            # empty stdout, when nothing is flagged.
            out.json(_plan_json(plan, csv_dir, stages, dry_run=dry_run))
        else:
            out.status(
                "[dim]no flagged directories matched [keep] — nothing to do.[/]"
            )
        return 0

    if dry_run:
        if out.json_mode:
            out.json(_plan_json(plan, csv_dir, stages, dry_run=True))
        return 0

    if not yes:
        # Destructive: never block on a prompt in a non-interactive session.
        if not out.interactive:
            out.error(
                "[red]error:[/] non-interactive session — pass -y to renew "
                f"{len(plan.kept)} directories, or -n to preview."
            )
            return 2
        ask = confirm_fn
        if ask is None:
            from rich.prompt import Confirm  # lazy: only the prompt path needs rich

            ask = Confirm.ask
        if not ask(
            f"Touch mtimes on {len(plan.kept)} directories?", default=False
        ):
            out.status("[dim]aborted[/]")
            return 1

    run = execute_fn or _execute
    total_files, failures = run(plan, jobs_n, out)

    if out.json_mode:
        summary = {
            "renewed": True,
            "dirs": len(plan.kept),
            "files_touched": total_files,
            "failures": failures,
            "kept_truncated": len(plan.kept) > JSON_LIST_CAP,
            "kept": [d for _, d in plan.kept[:JSON_LIST_CAP]],
        }
        if summary["kept_truncated"]:
            summary["full_plan_path"] = _dump_full_plan(plan, csv_dir, stages)
        out.json(summary)
    else:
        out.status(
            f"[green]done[/] {len(plan.kept)} dirs · "
            f"{total_files} files touched"
            + (f" · [red]{failures} failed[/]" if failures else "")
        )
    return 1 if failures else 0


def _report_plan(
    out: Out,
    plan: Plan,
    csv_dir: Path,
    stages: list[str],
    verbose: bool,
) -> None:
    """Print the plan summary to stderr (human) — stdout stays the data channel."""
    if out.json_mode:
        return
    out.status(
        f"[dim]csv-dir:[/] {csv_dir}  [dim]stages:[/] {', '.join(stages)}"
    )
    out.status(
        f"[bold]plan:[/] {len(plan.kept)} kept, {len(plan.skipped)} skipped"
    )
    if len(plan.kept) > JSON_LIST_CAP or len(plan.skipped) > JSON_LIST_CAP:
        path = _dump_full_plan(plan, csv_dir, stages)
        out.status(f"[dim]full plan ({len(plan.kept) + len(plan.skipped)} dirs):[/] {path}")
    if verbose:
        if plan.kept:
            out.status("[green]kept:[/]")
            for stage_name, d in plan.kept[:20]:
                out.status(f"  [dim]{stage_name:>9}[/] {d}")
            if len(plan.kept) > 20:
                out.status(f"  [dim]… and {len(plan.kept) - 20} more[/]")
        if plan.skipped:
            out.status(
                r"[yellow]skipped[/] (flagged by Sol but not in \[keep]):"
            )
            for stage_name, d in plan.skipped[:20]:
                out.status(f"  [dim]{stage_name:>9}[/] {d}")


def _plan_json(plan: Plan, csv_dir: Path, stages: list[str], *, dry_run: bool) -> dict:
    """Bounded plan document: exact counts, a capped sample of each list.

    When either list is truncated, the COMPLETE plan is spilled to a temp file
    and its path returned under ``full_plan_path`` — so the response stays small
    enough for an agent's context while the full detail is one ``cat`` away.
    """
    truncated = len(plan.kept) > JSON_LIST_CAP or len(plan.skipped) > JSON_LIST_CAP
    doc = {
        "dry_run": dry_run,
        "csv_dir": str(csv_dir),
        "stages": stages,
        "kept_count": len(plan.kept),
        "skipped_count": len(plan.skipped),
        "kept_truncated": len(plan.kept) > JSON_LIST_CAP,
        "skipped_truncated": len(plan.skipped) > JSON_LIST_CAP,
        "kept": [{"stage": s, "dir": d} for s, d in plan.kept[:JSON_LIST_CAP]],
        "skipped": [{"stage": s, "dir": d} for s, d in plan.skipped[:JSON_LIST_CAP]],
    }
    if truncated:
        doc["full_plan_path"] = _dump_full_plan(plan, csv_dir, stages)
    return doc


def _dump_full_plan(plan: Plan, csv_dir: Path, stages: list[str]) -> str:
    """Write the complete (untruncated) plan to a temp file; return its path."""
    fd, path = tempfile.mkstemp(prefix="solx-keep-plan-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(
            {
                "csv_dir": str(csv_dir),
                "stages": stages,
                "kept": [{"stage": s, "dir": d} for s, d in plan.kept],
                "skipped": [{"stage": s, "dir": d} for s, d in plan.skipped],
            },
            fh,
            indent=2,
        )
    return path


def _execute(
    plan: Plan,
    jobs_n: int,
    out: Out,
    *,
    enumerate_fn: Callable[[str], tuple[str, list[bytes], str]] | None = None,
    touch_fn: Callable[[list[bytes]], tuple[int, int, str]] | None = None,
) -> tuple[int, int]:
    """Renew `plan.kept` as a bounded streaming pipeline. Returns (files, failures).

    With ``jobs_n <= 1`` runs serially (no process pool — fast and deterministic
    for tests and small runs). Otherwise one worker pool runs both halves:
    enumerate a directory, shard its files, submit the batches as `touch` tasks,
    and top up enumeration only while the in-flight set has room. The bounded
    window keeps peak memory a small multiple of `jobs_n` batches and lets a
    single huge directory spread its batches over every worker.
    """
    enumerate_fn = enumerate_fn or enumerate_dir
    touch_fn = touch_fn or touch_files
    dirs = [d for _, d in plan.kept]
    total_files = 0
    enum_fail = touch_fail = 0

    if jobs_n <= 1:
        for d in dirs:
            try:
                _, files, msg = enumerate_fn(d)
            except Exception as e:  # noqa: BLE001 — never let one dir abort the run
                enum_fail += 1
                out.error(f"[red]FAIL[/] enumerate {d} :: {e}")
                continue
            if msg != "ok" and not msg.startswith("skipped"):
                enum_fail += 1
                out.error(f"[red]FAIL[/] enumerate {d} :: {msg}")
                continue
            for batch in shard(files):
                try:
                    n, errs, tmsg = touch_fn(batch)
                except Exception as e:  # noqa: BLE001
                    touch_fail += 1
                    out.error(f"[red]FAIL[/] touch {d} :: {e}")
                    continue
                total_files += n
                if errs:
                    touch_fail += 1
                    out.error(f"[red]FAIL[/] touch {d} :: {tmsg}")
            if msg == "ok" and not out.json_mode:
                out.status(f"  [dim]ok[/] {len(files):>7d} files  {d}")
        return total_files, enum_fail + touch_fail

    # Parallel: bounded streaming window over one pool.
    window = max(2 * jobs_n, jobs_n + 8)
    pending: dict = {}
    di = iter(dirs)

    with ProcessPoolExecutor(max_workers=jobs_n) as pool:

        def fill() -> None:
            while len(pending) < window:
                d = next(di, None)
                if d is None:
                    return
                pending[pool.submit(enumerate_fn, d)] = ("enum", d)

        fill()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                kind, d = pending.pop(fut)
                if kind == "enum":
                    try:
                        _, files, msg = fut.result()
                    except Exception as e:  # noqa: BLE001 — e.g. BrokenProcessPool
                        enum_fail += 1
                        out.error(f"[red]FAIL[/] enumerate {d} :: {e}")
                        continue
                    if msg == "ok":
                        for batch in shard(files):
                            pending[pool.submit(touch_fn, batch)] = ("touch", d)
                    elif not msg.startswith("skipped"):
                        enum_fail += 1
                        out.error(f"[red]FAIL[/] enumerate {d} :: {msg}")
                else:  # touch batch
                    try:
                        n, errs, tmsg = fut.result()
                    except Exception as e:  # noqa: BLE001
                        touch_fail += 1
                        out.error(f"[red]FAIL[/] touch {d} :: {e}")
                        continue
                    total_files += n
                    if errs:
                        touch_fail += 1
                        out.error(f"[red]FAIL[/] touch {d} :: {tmsg}")
            fill()
    return total_files, enum_fail + touch_fail

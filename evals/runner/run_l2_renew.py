#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
L2 eval: run the real `sol_renew.py` against a generated mock scratch tree
and assert on the filesystem mutations + exit codes it produces.

Why this exists: the static mocks under `evals/mocks/` let L1 (transcript)
checks exercise CSV/`.solkeep` parsing, but their CSVs point at absolute
`/scratch/sparky/...` paths that don't exist on a test box, so they can't
prove the script actually *touches* the right files. This builds a self-
contained sandbox with real files and stale mtimes, points a `.solkeep` and a
warning CSV at it, runs the script, and verifies the behaviors the skill
promises:

  - dry-run touches nothing
  - kept files (recursively) get their mtime refreshed
  - `.solkeep` carve-outs (`.venv`, `__pycache__`, ...) are NOT touched
  - a flagged dir that isn't in `.solkeep` is skipped
  - a flagged dir that no longer exists is benign (exit stays 0)

Run it directly (self-bootstraps via uv):

    evals/runner/run_l2_renew.py            # assert; exit 1 on any failure
    evals/runner/run_l2_renew.py -v         # also echo the script's output

Exit code is 0 when every assertion passes, 1 otherwise -- so it drops into
CI or the L2 layer of the eval harness as-is.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "skills" / "sol-skill" / "scripts" / "sol_renew.py"

# A stale mtime well in the past; a refreshed file lands at ~now, so any
# threshold between the two cleanly separates "touched" from "left alone".
STALE = 1_400_000_000  # 2014-05-13


def _build_sandbox(root: Path) -> tuple[Path, Path]:
    """Lay out a scratch tree, a .solkeep, and a warning CSV pointing at it.

    Returns (csv_dir, solkeep_path). Every file starts at STALE mtime.

    Sol flags *leaf* directories, so the CSV lists leaves -- never a parent
    that contains another flagged row. That matters: the tool walks a kept
    directory recursively, so a `.solkeep` carve-out only takes effect when the
    carved tree is its own flagged row (not swept up by a kept ancestor's
    walk). The layout below mirrors that: `proj/src` is the kept leaf, while
    `proj/.venv/lib` and `proj/__pycache__` are sibling leaves the carve-outs
    drop.
    """
    scratch = root / "scratch"
    files = [
        scratch / "proj" / "src" / "a.txt",            # kept leaf -> touch
        scratch / "proj" / "src" / "nested" / "b.txt",  # recursion -> touch
        scratch / "proj" / ".venv" / "lib" / "x.py",   # carve-out leaf -> skip
        scratch / "proj" / "__pycache__" / "c.pyc",     # carve-out leaf -> skip
        scratch / "other" / "d.txt",                   # not in .solkeep -> skip
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
        os.utime(f, (STALE, STALE))

    solkeep = root / "solkeep"
    solkeep.write_text(
        f"{scratch}/proj\n"
        f"!{scratch}/proj/**/.venv/**\n"
        f"!{scratch}/proj/**/__pycache__\n"
    )

    # CSV columns mirror Sol's; the script only reads `Directory`. Rows are
    # leaves: the kept tree, the two carve-out trees (the run must decide to
    # skip them), a flagged dir outside .solkeep, and one already removed.
    csv = root / "scratch-dirs-pending-removal.csv"
    rows = [
        scratch / "proj" / "src",
        scratch / "proj" / ".venv" / "lib",
        scratch / "proj" / "__pycache__",
        scratch / "other",
        scratch / "ghost-already-removed",
    ]
    lines = ["Directory,Last Used,File Count"]
    lines += [f"{d},2026-01-01,1" for d in rows]
    csv.write_text("\n".join(lines) + "\n")
    return root, solkeep


def _run(csv_dir: Path, solkeep: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault(
        "UV_CACHE_DIR",
        f"/scratch/{env.get('USER', 'nobody')}/.cache/uv",
    )
    cmd = [
        "uv", "run", "--script", str(SCRIPT),
        "--stage", "pending",
        "--csv-dir", str(csv_dir),
        "--solkeep", str(solkeep),
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main() -> int:
    verbose = "-v" in sys.argv[1:]
    if not SCRIPT.exists():
        print(f"FAIL: sol_renew.py not found at {SCRIPT}")
        return 1

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="sol-l2-") as tmp:
        root = Path(tmp)
        csv_dir, solkeep = _build_sandbox(root)
        scratch = root / "scratch"

        def mtime(*parts: str) -> float:
            return os.path.getmtime(scratch.joinpath(*parts))

        # --- dry-run must touch nothing -----------------------------------
        before = time.time()
        dry = _run(csv_dir, solkeep, "--dry-run")
        if verbose:
            print("--- dry-run ---\n" + dry.stdout + dry.stderr)
        check("dry-run exits 0", dry.returncode == 0, f"rc={dry.returncode}")
        check(
            "dry-run touches nothing",
            mtime("proj", "src", "a.txt") < before,
            "a.txt mtime advanced during --dry-run",
        )

        # --- real run ------------------------------------------------------
        before = time.time()
        run = _run(csv_dir, solkeep)
        if verbose:
            print("--- run ---\n" + run.stdout + run.stderr)
        check("run exits 0", run.returncode == 0, f"rc={run.returncode}")
        check(
            "kept file refreshed",
            mtime("proj", "src", "a.txt") >= before,
            "proj/src/a.txt was not touched",
        )
        check(
            "kept nested file refreshed (recursion)",
            mtime("proj", "src", "nested", "b.txt") >= before,
            "proj/src/nested/b.txt was not touched",
        )
        check(
            ".venv carve-out left alone",
            mtime("proj", ".venv", "lib", "x.py") < before,
            ".venv file was touched despite carve-out",
        )
        check(
            "__pycache__ carve-out left alone",
            mtime("proj", "__pycache__", "c.pyc") < before,
            "__pycache__ file was touched despite carve-out",
        )
        check(
            "non-kept dir skipped",
            mtime("other", "d.txt") < before,
            "other/d.txt was touched but is not in .solkeep",
        )

    width = max(len(n) for n, _, _ in checks)
    passed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {name.ljust(width)}"
        if not ok and detail:
            line += f"  -- {detail}"
        print(line)
        passed += ok
    total = len(checks)
    print(f"\nL2 renew: {passed}/{total} assertions passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

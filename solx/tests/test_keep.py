from __future__ import annotations

import os
import time
from io import StringIO
from pathlib import Path

import pathspec
import pytest
from rich.console import Console

from solx import keep as keep_mod
from solx.config import Config, JobTemplate, KeepRules
from solx.output import Out


def make_out(*, json_mode: bool = False, interactive: bool = True) -> Out:
    so = Console(file=StringIO(), force_terminal=False, width=200)
    se = Console(file=StringIO(), force_terminal=False, width=200)
    return Out(json_mode=json_mode, interactive=interactive, stdout=so, stderr=se)


def make_config(*, keep: KeepRules | None = None) -> Config:
    return Config(
        default_shell="bash",
        default_template="default",
        start_timeout_seconds=600,
        templates={
            "default": JobTemplate(name="default", partition="lightwork", time="1-0")
        },
        keep=keep,
    )


def make_keep(*, include: list[str], exclude: list[str] | None = None) -> KeepRules:
    return KeepRules(
        include=pathspec.GitIgnoreSpec.from_lines(include),
        exclude=pathspec.GitIgnoreSpec.from_lines(exclude or []),
        raw_include=tuple(include),
        raw_exclude=tuple(exclude or []),
    )


def write_csv(path: Path, dirs: list[str]) -> None:
    lines = ["Directory,LastAccess,Size"]
    lines += [f"{d},2026-01-01,1G" for d in dirs]
    path.write_text("\n".join(lines) + "\n")


# A stub execute_fn that records which directories the plan would touch and
# returns (files_touched, failures). Replaces the real process pool.
def recording_execute(record: list[str], *, files_each: int = 1, failures: int = 0):
    def _execute(plan, jobs_n, out):
        record.extend(d for _, d in plan.kept)
        return len(plan.kept) * files_each, failures

    return _execute


# ---- planning ------------------------------------------------------------


def test_load_csv_dirs(tmp_path: Path) -> None:
    p = tmp_path / "scratch-dirs-pending-removal.csv"
    write_csv(p, ["/scratch/sparky/a", "/scratch/sparky/b"])
    assert keep_mod.load_csv_dirs(p) == [
        "/scratch/sparky/a",
        "/scratch/sparky/b",
    ]


def test_load_csv_dirs_missing(tmp_path: Path) -> None:
    assert keep_mod.load_csv_dirs(tmp_path / "absent.csv") == []


def test_build_plan_filters_by_keep(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv",
        ["/scratch/sparky/proj-a", "/scratch/sparky/proj-z"],
    )
    write_csv(
        tmp_path / "scratch-dirs-over-90days.csv",
        ["/scratch/sparky/proj-b"],
    )
    keep = make_keep(
        include=["/scratch/sparky/proj-a", "/scratch/sparky/proj-b"],
    )
    plan = keep_mod.build_plan(tmp_path, list(keep_mod.STAGE_ORDER), keep)
    assert {d for _, d in plan.kept} == {
        "/scratch/sparky/proj-a",
        "/scratch/sparky/proj-b",
    }
    assert {d for _, d in plan.skipped} == {"/scratch/sparky/proj-z"}


def test_build_plan_dedupes_across_stages(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    write_csv(tmp_path / "scratch-dirs-over-90days.csv", ["/scratch/sparky/a"])
    keep = make_keep(include=["/scratch/sparky/a"])
    plan = keep_mod.build_plan(tmp_path, list(keep_mod.STAGE_ORDER), keep)
    assert len(plan.kept) == 1


def test_build_plan_exclude_carve_out(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv",
        [
            "/scratch/sparky/proj/run-1",
            "/scratch/sparky/proj/__pycache__",
        ],
    )
    keep = make_keep(
        include=["/scratch/sparky/proj/**"],
        exclude=["**/__pycache__"],
    )
    plan = keep_mod.build_plan(tmp_path, ["pending"], keep)
    assert {d for _, d in plan.kept} == {"/scratch/sparky/proj/run-1"}
    assert {d for _, d in plan.skipped} == {"/scratch/sparky/proj/__pycache__"}


# ---- shard / enumerate / touch (the renewal mechanism) -------------------


def test_shard_even_batches() -> None:
    files = [bytes([i]) for i in range(0, 10)]
    batches = keep_mod.shard(files, batch_size=3)
    assert [len(b) for b in batches] == [3, 3, 3, 1]
    assert sum(batches, []) == files


def test_shard_empty() -> None:
    assert keep_mod.shard([]) == []


def test_enumerate_dir_lists_all_including_hidden_and_ignored(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("x")
    # A .gitignore plus an ignored file: --no-ignore must still list it.
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "ignored.txt").write_text("x")

    directory, files, msg = keep_mod.enumerate_dir(str(tmp_path))
    assert msg == "ok"
    assert all(os.path.isfile(p) for p in files)
    # 5 regular files: a.txt, .hidden, sub/b.txt, .gitignore, ignored.txt
    assert len(files) == 5


def test_enumerate_dir_not_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    _, files, msg = keep_mod.enumerate_dir(str(missing))
    assert files == []
    assert msg.startswith("skipped")


def test_touch_files_refreshes_mtime(tmp_path: Path) -> None:
    f = tmp_path / "stale.txt"
    f.write_text("x")
    old = time.time() - 60 * 60 * 24 * 100  # 100 days ago
    os.utime(f, (old, old))
    assert f.stat().st_mtime < time.time() - 1000

    attempted, errors, msg = keep_mod.touch_files([str(f).encode()])
    assert errors == 0
    assert attempted == 1
    assert f.stat().st_mtime > time.time() - 10


def test_touch_files_empty_batch() -> None:
    assert keep_mod.touch_files([]) == (0, 0, "ok")


def test_execute_survives_raising_enumerate() -> None:
    """A worker that raises is counted as a failure, not an uncaught crash."""
    plan = keep_mod.Plan(kept=[("pending", "/scratch/sparky/a")])

    def boom(_d):
        raise RuntimeError("worker died")

    total, failures = keep_mod._execute(plan, 1, make_out(), enumerate_fn=boom)
    assert (total, failures) == (0, 1)


def test_execute_survives_raising_touch() -> None:
    plan = keep_mod.Plan(kept=[("pending", "/scratch/sparky/a")])

    def enum_ok(d):
        return (d, [b"/scratch/sparky/a/f1"], "ok")

    def boom(_batch):
        raise RuntimeError("touch died")

    total, failures = keep_mod._execute(
        plan, 1, make_out(), enumerate_fn=enum_ok, touch_fn=boom
    )
    assert (total, failures) == (0, 1)


# ---- cmd_keep ------------------------------------------------------------


def test_keep_yes_and_dry_run_mutually_exclusive(tmp_path: Path) -> None:
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=True, dry_run=True, verbose=False, out=make_out(),
    )
    assert code == 2


def test_keep_no_keep_block_exits_2(tmp_path: Path) -> None:
    cfg = make_config(keep=None)
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=False, verbose=False, out=make_out(),
    )
    assert code == 2


def test_keep_dry_run_does_not_execute(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    touched: list[str] = []
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=True, verbose=False, out=make_out(),
        execute_fn=recording_execute(touched),
    )
    assert code == 0
    assert touched == []


def test_keep_executes_with_yes(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv",
        ["/scratch/sparky/a", "/scratch/sparky/b"],
    )
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/**"]))
    touched: list[str] = []
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=True, dry_run=False, verbose=False, out=make_out(),
        execute_fn=recording_execute(touched, files_each=5),
    )
    assert code == 0
    assert sorted(touched) == ["/scratch/sparky/a", "/scratch/sparky/b"]


def test_keep_prompts_and_aborts(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    touched: list[str] = []
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=False, verbose=False, out=make_out(interactive=True),
        confirm_fn=lambda *a, **kw: False,
        execute_fn=recording_execute(touched),
    )
    assert code == 1
    assert touched == []


def test_keep_prompts_and_proceeds(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    touched: list[str] = []
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=False, verbose=False, out=make_out(interactive=True),
        confirm_fn=lambda *a, **kw: True,
        execute_fn=recording_execute(touched),
    )
    assert code == 0
    assert touched == ["/scratch/sparky/a"]


def test_keep_non_interactive_refuses(tmp_path: Path) -> None:
    """No TTY on stdin and no -y/-n -> refuse with exit 2, never prompt."""
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    touched: list[str] = []
    out = make_out(interactive=False)
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=False, verbose=False, out=out,
        confirm_fn=lambda *a, **kw: pytest.fail("must not prompt"),
        execute_fn=recording_execute(touched),
    )
    assert code == 2
    assert touched == []
    assert "non-interactive" in out.stderr.file.getvalue()


def test_keep_no_matches_no_prompt(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/z"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    confirms: list = []
    touched: list[str] = []
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=False, verbose=False, out=make_out(),
        confirm_fn=lambda *a, **kw: confirms.append(True) or True,
        execute_fn=recording_execute(touched),
    )
    assert code == 0
    assert confirms == []
    assert touched == []


def test_keep_specific_stage_only(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/p"])
    write_csv(tmp_path / "scratch-dirs-over-90days.csv", ["/scratch/sparky/o"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/**"]))
    touched: list[str] = []
    keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="pending", jobs_n=1,
        yes=True, dry_run=False, verbose=False, out=make_out(),
        execute_fn=recording_execute(touched),
    )
    assert touched == ["/scratch/sparky/p"]


def test_keep_propagates_failures(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=True, dry_run=False, verbose=False, out=make_out(),
        execute_fn=lambda plan, jobs_n, out: (0, 1),
    )
    assert code == 1


def test_keep_json_summary(tmp_path: Path) -> None:
    import json as _json

    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    out = make_out(json_mode=True, interactive=False)
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=True, dry_run=False, verbose=False, out=out,
        execute_fn=lambda plan, jobs_n, out: (7, 0),
    )
    assert code == 0
    data = _json.loads(out.stdout.file.getvalue())
    assert data["files_touched"] == 7
    assert data["dirs"] == 1
    assert data["kept"] == ["/scratch/sparky/a"]


def test_keep_dry_run_json_plan(tmp_path: Path) -> None:
    import json as _json

    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv",
        ["/scratch/sparky/a", "/scratch/sparky/z"],
    )
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    out = make_out(json_mode=True, interactive=False)
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=True, verbose=False, out=out,
    )
    assert code == 0
    data = _json.loads(out.stdout.file.getvalue())
    assert data["dry_run"] is True
    assert [k["dir"] for k in data["kept"]] == ["/scratch/sparky/a"]
    assert [s["dir"] for s in data["skipped"]] == ["/scratch/sparky/z"]
    assert data["kept_count"] == 1 and data["skipped_count"] == 1
    assert data["kept_truncated"] is False


def test_keep_json_plan_bounded(tmp_path: Path) -> None:
    """Sol can flag thousands of dirs; JSON inlines a capped sample + exact counts."""
    import json as _json

    n = keep_mod.JSON_LIST_CAP + 50
    dirs = [f"/scratch/sparky/proj/run-{i:04d}" for i in range(n)]
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", dirs)
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/proj/**"]))
    out = make_out(json_mode=True, interactive=False)
    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=True, verbose=False, out=out,
    )
    assert code == 0
    data = _json.loads(out.stdout.file.getvalue())
    assert data["kept_count"] == n              # exact total
    assert data["kept_truncated"] is True
    assert len(data["kept"]) == keep_mod.JSON_LIST_CAP  # sample is capped
    # full detail spilled to a temp file whose path is returned
    full_path = data["full_plan_path"]
    assert os.path.exists(full_path)
    full = _json.load(open(full_path))
    assert len(full["kept"]) == n               # complete list on disk
    os.unlink(full_path)


def test_keep_json_plan_small_no_spill(tmp_path: Path) -> None:
    """A small plan stays inline with no temp file."""
    import json as _json

    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    out = make_out(json_mode=True, interactive=False)
    keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=False, dry_run=True, verbose=False, out=out,
    )
    data = _json.loads(out.stdout.file.getvalue())
    assert "full_plan_path" not in data


# ---- end-to-end: real filesystem mutation (mirrors run_l2_renew.py) ------


def test_keep_end_to_end_real_touch(tmp_path: Path) -> None:
    """Build a real scratch tree with stale mtimes; cmd_keep refreshes the
    kept files recursively and leaves carve-outs / non-kept dirs alone.

    Sol flags *leaf* directories, so the CSV lists leaves — never a parent
    that contains another flagged row. A kept dir is walked recursively, so a
    carve-out only protects a tree when it is its own flagged row (a sibling
    leaf), mirroring evals/runner/run_l2_renew.py.
    """
    scratch = tmp_path / "scratch"
    src = scratch / "proj" / "src"
    pycache = scratch / "proj" / "__pycache__"
    other = scratch / "other"
    (src / "nested").mkdir(parents=True)
    pycache.mkdir(parents=True)
    other.mkdir(parents=True)

    kept_file = src / "keep-me.bin"
    nested_file = src / "nested" / "deep.bin"   # recursion within a kept leaf
    carve_file = pycache / "skip.pyc"           # carve-out sibling leaf
    other_file = other / "not-flagged.bin"      # never in [keep]
    for f in (kept_file, nested_file, carve_file, other_file):
        f.write_text("x")

    stale = time.time() - 60 * 60 * 24 * 100
    for f in (kept_file, nested_file, carve_file, other_file):
        os.utime(f, (stale, stale))

    # Leaves only: kept tree, the carve-out sibling, and an unkept dir.
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv",
        [str(src), str(pycache), str(other)],
    )
    cfg = make_config(
        keep=make_keep(include=[f"{scratch}/proj/**"], exclude=["**/__pycache__"])
    )

    code = keep_mod.cmd_keep(
        config=cfg, csv_dir=tmp_path, stage="all", jobs_n=1,
        yes=True, dry_run=False, verbose=False, out=make_out(),
    )
    assert code == 0

    now = time.time()
    assert kept_file.stat().st_mtime > now - 30        # kept leaf renewed
    assert nested_file.stat().st_mtime > now - 30      # recursion renewed
    assert carve_file.stat().st_mtime < now - 1000     # carve-out untouched
    assert other_file.stat().st_mtime < now - 1000     # non-kept untouched

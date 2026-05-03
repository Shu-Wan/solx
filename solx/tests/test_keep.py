from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from solx import keep as keep_mod
from solx.config import Config, JobTemplate, KeepRules
import pathspec


def silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False, width=200)


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
    plan = keep_mod.build_plan(
        tmp_path, list(keep_mod.STAGE_ORDER), keep
    )
    assert {d for _, d in plan.kept} == {
        "/scratch/sparky/proj-a",
        "/scratch/sparky/proj-b",
    }
    assert {d for _, d in plan.skipped} == {"/scratch/sparky/proj-z"}


def test_build_plan_dedupes_across_stages(tmp_path: Path) -> None:
    """Same dir flagged in multiple CSVs should appear only once."""
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


# ---- cmd_keep ------------------------------------------------------------


def test_keep_yes_and_dry_run_mutually_exclusive(tmp_path: Path) -> None:
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=True,
        dry_run=True,
        verbose=False,
        console=silent_console(),
    )
    assert code == 2


def test_keep_no_keep_block_exits_2(tmp_path: Path) -> None:
    cfg = make_config(keep=None)
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=False,
        dry_run=False,
        verbose=False,
        console=silent_console(),
    )
    assert code == 2


def test_keep_dry_run_does_not_touch(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))

    touched: list = []
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=False,
        dry_run=True,
        verbose=False,
        console=silent_console(),
        touch_fn=lambda d: touched.append(d) or (d, 0, 0, "ok"),
    )
    assert code == 0
    assert touched == []  # never called


def test_keep_executes_with_yes(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv",
        ["/scratch/sparky/a", "/scratch/sparky/b"],
    )
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/**"]))

    touched: list = []
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=True,
        dry_run=False,
        verbose=False,
        console=silent_console(),
        touch_fn=lambda d: touched.append(d) or (d, 5, 0, "ok"),
    )
    assert code == 0
    assert sorted(touched) == ["/scratch/sparky/a", "/scratch/sparky/b"]


def test_keep_prompts_and_aborts(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))

    touched: list = []
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=False,
        dry_run=False,
        verbose=False,
        console=silent_console(),
        confirm_fn=lambda *a, **kw: False,
        touch_fn=lambda d: touched.append(d) or (d, 1, 0, "ok"),
    )
    assert code == 1
    assert touched == []


def test_keep_prompts_and_proceeds(tmp_path: Path) -> None:
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))

    touched: list = []
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=False,
        dry_run=False,
        verbose=False,
        console=silent_console(),
        confirm_fn=lambda *a, **kw: True,
        touch_fn=lambda d: touched.append(d) or (d, 1, 0, "ok"),
    )
    assert code == 0
    assert touched == ["/scratch/sparky/a"]


def test_keep_no_matches_no_prompt(tmp_path: Path) -> None:
    """If [keep] filters out everything, no prompt and no execute path."""
    write_csv(tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/z"])
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))

    confirms: list = []
    touched: list = []
    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=False,
        dry_run=False,
        verbose=False,
        console=silent_console(),
        confirm_fn=lambda *a, **kw: confirms.append(True) or True,
        touch_fn=lambda d: touched.append(d) or (d, 1, 0, "ok"),
    )
    assert code == 0
    assert confirms == []  # nothing to ask about
    assert touched == []


def test_keep_specific_stage_only(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/p"]
    )
    write_csv(
        tmp_path / "scratch-dirs-over-90days.csv", ["/scratch/sparky/o"]
    )
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/**"]))

    touched: list = []
    keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="pending",
        jobs_n=1,
        yes=True,
        dry_run=False,
        verbose=False,
        console=silent_console(),
        touch_fn=lambda d: touched.append(d) or (d, 1, 0, "ok"),
    )
    assert touched == ["/scratch/sparky/p"]


def test_keep_propagates_touch_errors(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "scratch-dirs-pending-removal.csv", ["/scratch/sparky/a"]
    )
    cfg = make_config(keep=make_keep(include=["/scratch/sparky/a"]))

    code = keep_mod.cmd_keep(
        config=cfg,
        csv_dir=tmp_path,
        stage="all",
        jobs_n=1,
        yes=True,
        dry_run=False,
        verbose=False,
        console=silent_console(),
        touch_fn=lambda d: (d, 0, 1, "permission denied"),
    )
    assert code == 1

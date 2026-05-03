from __future__ import annotations

import stat
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from solx import init as init_mod
from solx import config as cfg


def silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False, width=200)


def test_init_writes_fresh_config(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    code = init_mod.cmd_init(path=p, force=False, console=silent_console())
    assert code == 0
    assert p.exists()
    # Round-trips via load:
    loaded = cfg.load(p)
    assert loaded.default_template == "default"


def test_init_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "config" / "solx" / "config.toml"
    code = init_mod.cmd_init(path=p, force=False, console=silent_console())
    assert code == 0
    assert p.exists()


def test_init_mode_0600(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    init_mod.cmd_init(path=p, force=False, console=silent_console())
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_init_refuses_existing_without_force(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("# existing user config\n")
    code = init_mod.cmd_init(
        path=p,
        force=False,
        console=silent_console(),
        confirm_fn=lambda *a, **kw: False,
    )
    assert code == 1
    assert p.read_text() == "# existing user config\n"  # unchanged


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("# old\n")
    code = init_mod.cmd_init(path=p, force=True, console=silent_console())
    assert code == 0
    assert "default_template" in p.read_text()


def test_init_overwrites_when_user_confirms(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("# old\n")
    code = init_mod.cmd_init(
        path=p,
        force=False,
        console=silent_console(),
        confirm_fn=lambda *a, **kw: True,
    )
    assert code == 0
    assert "default_template" in p.read_text()

from __future__ import annotations

import json
import stat
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from solx import init as init_mod
from solx import config as cfg
from solx.output import Out


def make_out(*, json_mode: bool = False, interactive: bool = True) -> Out:
    so = Console(file=StringIO(), force_terminal=False, width=200)
    se = Console(file=StringIO(), force_terminal=False, width=200)
    return Out(json_mode=json_mode, interactive=interactive, stdout=so, stderr=se)


def test_init_writes_fresh_config(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    code = init_mod.cmd_init(path=p, force=False, out=make_out())
    assert code == 0
    assert p.exists()
    # Round-trips via load:
    loaded = cfg.load(p)
    assert loaded.default_template == "default"


def test_init_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "config" / "solx" / "config.toml"
    code = init_mod.cmd_init(path=p, force=False, out=make_out())
    assert code == 0
    assert p.exists()


def test_init_mode_0600(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    init_mod.cmd_init(path=p, force=False, out=make_out())
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_init_json_mode(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    out = make_out(json_mode=True, interactive=False)
    code = init_mod.cmd_init(path=p, force=False, out=out)
    assert code == 0
    assert json.loads(out.stdout.file.getvalue()) == {"wrote": str(p)}


def test_init_refuses_existing_without_force(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("# existing user config\n")
    code = init_mod.cmd_init(
        path=p,
        force=False,
        out=make_out(interactive=True),
        confirm_fn=lambda *a, **kw: False,
    )
    assert code == 1
    assert p.read_text() == "# existing user config\n"  # unchanged


def test_init_non_interactive_existing_refuses(tmp_path: Path) -> None:
    """No TTY + existing config + no -f -> exit 2, never prompt, never overwrite."""
    p = tmp_path / "config.toml"
    p.write_text("# existing user config\n")
    out = make_out(interactive=False)
    code = init_mod.cmd_init(
        path=p,
        force=False,
        out=out,
        confirm_fn=lambda *a, **kw: pytest.fail("must not prompt"),
    )
    assert code == 2
    assert p.read_text() == "# existing user config\n"
    assert "-f" in out.stderr.file.getvalue()


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("# old\n")
    code = init_mod.cmd_init(path=p, force=True, out=make_out())
    assert code == 0
    assert "default_template" in p.read_text()


def test_init_overwrites_when_user_confirms(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("# old\n")
    code = init_mod.cmd_init(
        path=p,
        force=False,
        out=make_out(interactive=True),
        confirm_fn=lambda *a, **kw: True,
    )
    assert code == 0
    assert "default_template" in p.read_text()

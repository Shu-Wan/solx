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


def make_out(*, json_mode: bool = False, interactive: bool = False) -> Out:
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
        walkthrough_fn=lambda out, sk: None,  # skip the walkthrough
    )
    assert code == 0
    assert "default_template" in p.read_text()


def test_init_walkthrough_picks_shell(tmp_path: Path) -> None:
    """An interactive walkthrough that picks a shell sets default_shell."""
    p = tmp_path / "config.toml"
    code = init_mod.cmd_init(
        path=p, force=False, out=make_out(interactive=True),
        walkthrough_fn=lambda out, sk: {"shell": "zsh", "keep": None},
    )
    assert code == 0
    assert cfg.load(p).default_shell == "zsh"


def test_init_walkthrough_declined_keeps_default(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    init_mod.cmd_init(
        path=p, force=False, out=make_out(interactive=True),
        walkthrough_fn=lambda out, sk: None,  # declined
    )
    assert cfg.load(p).default_shell == "bash"


def test_init_no_walkthrough_when_noninteractive(tmp_path: Path) -> None:
    """A non-interactive session never runs the walkthrough (no silent import)."""
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n")
    p = tmp_path / "config.toml"
    init_mod.cmd_init(
        path=p, force=False, solkeep=solkeep, out=make_out(interactive=False),
        walkthrough_fn=lambda out, sk: pytest.fail("walkthrough must not run"),
    )
    c = cfg.load(p)
    assert c.default_shell == "bash"
    assert c.keep is None  # nothing imported without the prompt


def test_init_walkthrough_imports_solkeep(tmp_path: Path) -> None:
    """The walkthrough's import step carries ~/.solkeep into [keep]."""
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n!**/__pycache__\n")
    cfgpath = tmp_path / "config.toml"
    out = make_out(interactive=True)
    code = init_mod.cmd_init(
        path=cfgpath, force=False, solkeep=solkeep, out=out,
        walkthrough_fn=lambda o, sk: {"shell": "bash", "keep": cfg.import_solkeep(sk)},
    )
    assert code == 0
    c = cfg.load(cfgpath)
    assert c.keep is not None
    assert c.keep.matches("/scratch/sparky/proj/x")
    assert not c.keep.matches("/scratch/sparky/proj/x/__pycache__")
    assert "imported" in out.stderr.file.getvalue()


def test_default_walkthrough_prompts_import_and_shell(tmp_path: Path, monkeypatch) -> None:
    """The real walkthrough asks to import .solkeep, then picks a shell."""
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n")
    monkeypatch.setattr(init_mod.Confirm, "ask", lambda *a, **kw: True)  # walkthrough + import
    monkeypatch.setattr(init_mod.Prompt, "ask", lambda *a, **kw: "zsh")
    res = init_mod._default_walkthrough(make_out(interactive=True), solkeep)
    assert res == {"shell": "zsh", "keep": (["/scratch/sparky/proj"], [])}


def test_default_walkthrough_declines_import(tmp_path: Path, monkeypatch) -> None:
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n")
    answers = iter([True, False])  # walkthrough yes, import no
    monkeypatch.setattr(init_mod.Confirm, "ask", lambda *a, **kw: next(answers))
    monkeypatch.setattr(init_mod.Prompt, "ask", lambda *a, **kw: "bash")
    res = init_mod._default_walkthrough(make_out(interactive=True), solkeep)
    assert res == {"shell": "bash", "keep": None}


def test_default_walkthrough_declined(monkeypatch) -> None:
    monkeypatch.setattr(init_mod.Confirm, "ask", lambda *a, **kw: False)
    assert init_mod._default_walkthrough(make_out(interactive=True), None) is None


def test_init_no_solkeep_keeps_placeholder(tmp_path: Path) -> None:
    """With no walkthrough/import, the starter keeps the commented [keep] placeholder."""
    cfgpath = tmp_path / "config.toml"
    init_mod.cmd_init(
        path=cfgpath, force=False, solkeep=tmp_path / "absent", out=make_out()
    )
    c = cfg.load(cfgpath)
    assert c.keep is None
    assert "sparky" in cfgpath.read_text()

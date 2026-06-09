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


# ---- config import-solkeep (the .solkeep -> [keep] migration) ------------

_CONFIG_NO_KEEP = """\
default_shell = "bash"
default_template = "default"

[jobs.default]
partition = "lightwork"
time = "1-0"
"""


def test_import_solkeep_appends_keep_block(tmp_path: Path) -> None:
    """A config without [keep] + a ~/.solkeep -> a [keep] block is appended."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n!**/__pycache__\n")

    out = make_out()
    code = init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=out)
    assert code == 0
    c = cfg.load(cfgpath)
    assert c.keep is not None
    assert c.keep.matches("/scratch/sparky/proj/x")
    assert not c.keep.matches("/scratch/sparky/proj/x/__pycache__")
    assert "imported" in out.stderr.file.getvalue()


def test_import_solkeep_refuses_when_keep_exists(tmp_path: Path) -> None:
    """A config that already has [keep] is left alone (a 2nd table is invalid TOML)."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(
        _CONFIG_NO_KEEP + '\n[keep]\ninclude = ["/scratch/sparky/existing"]\n'
    )
    before = cfgpath.read_text()
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n")

    out = make_out()
    code = init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=out)
    assert code == 2
    assert cfgpath.read_text() == before  # untouched
    assert "already has" in out.stderr.file.getvalue()


def test_import_solkeep_no_config_exits_2(tmp_path: Path) -> None:
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n")
    out = make_out()
    code = init_mod.cmd_import_solkeep(
        path=tmp_path / "absent.toml", solkeep=solkeep, out=out
    )
    assert code == 2
    assert "solx init" in out.stderr.file.getvalue()


def test_import_solkeep_no_patterns_exits_2(tmp_path: Path) -> None:
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("# just a comment\n\n")
    out = make_out()
    code = init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=out)
    assert code == 2
    assert cfg.load(cfgpath).keep is None  # nothing appended


def test_import_solkeep_escapes_control_char(tmp_path: Path) -> None:
    """A pattern with a control byte is escaped, not left to corrupt the config."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/a\x1bb\n")  # interior ESC
    code = init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=make_out())
    assert code == 0
    c = cfg.load(cfgpath)  # must still parse — no corruption on disk
    assert c.keep is not None
    assert "/scratch/sparky/a\x1bb" in c.keep.raw_include


_ORDER_SENSITIVE_SOLKEEP = (
    "/scratch/sparky/proj\n"
    "!/scratch/sparky/proj/big\n"
    "/scratch/sparky/proj/big/keep\n"  # re-include AFTER the carve-out
)


def test_import_solkeep_order_sensitive_refuses_without_force(tmp_path: Path) -> None:
    """A lossy re-include is refused (exit 2, nothing written) unless -f."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text(_ORDER_SENSITIVE_SOLKEEP)
    out = make_out()
    code = init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=out)
    assert code == 2
    assert "carve-out" in out.stderr.file.getvalue()
    assert cfg.load(cfgpath).keep is None  # nothing written


def test_import_solkeep_order_sensitive_force_writes_with_warning(tmp_path: Path) -> None:
    """With -f the lossy import proceeds but warns that ordering isn't preserved."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text(_ORDER_SENSITIVE_SOLKEEP)
    out = make_out()
    code = init_mod.cmd_import_solkeep(
        path=cfgpath, solkeep=solkeep, force=True, out=out
    )
    assert code == 0
    assert "warning" in out.stderr.file.getvalue()
    assert cfg.load(cfgpath).keep is not None


def test_import_solkeep_faithful_shape_no_warn(tmp_path: Path) -> None:
    """Includes-then-carve-outs (the safe shape) migrates without a warning."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n!**/__pycache__\n")
    out = make_out()
    init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=out)
    assert "warning" not in out.stderr.file.getvalue()


def test_import_solkeep_bare_bang_dropped(tmp_path: Path) -> None:
    """A bare `!` carves nothing and must not become an empty exclude pattern."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / ".solkeep"
    solkeep.write_text("/scratch/sparky/proj\n! \n")
    code = init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=make_out())
    assert code == 0
    c = cfg.load(cfgpath)
    assert "" not in (c.keep.raw_exclude or ())


def test_import_solkeep_records_source_path(tmp_path: Path) -> None:
    """Importing from a non-default path records that path in the block comment."""
    cfgpath = tmp_path / "config.toml"
    cfgpath.write_text(_CONFIG_NO_KEEP)
    solkeep = tmp_path / "mykeep.txt"
    solkeep.write_text("/scratch/sparky/proj\n")
    init_mod.cmd_import_solkeep(path=cfgpath, solkeep=solkeep, out=make_out())
    assert str(solkeep) in cfgpath.read_text()  # provenance comment names the real source

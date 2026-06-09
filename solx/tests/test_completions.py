"""Shape and syntax coverage for the static completion scripts.

The scripts are fully static (no callback into solx at completion time), so
the tests assert on their text — every command listed, the right registration
footer per shell — and, where the shell is installed, run its syntax checker
over the emitted script.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from solx import _completions

TOP_COMMANDS = [
    "init", "keep", "jump", "job", "config", "completions", "version", "help",
]
JOB_SUBCOMMANDS = ["list", "start", "stop", "jump", "time"]
CONFIG_SUBCOMMANDS = ["show", "edit", "import-solkeep"]

SCRIPTS = {
    "bash": _completions.bash_script,
    "zsh": _completions.zsh_script,
    "fish": _completions.fish_script,
}


# ---- golden-shape assertions ---------------------------------------------


def test_zsh_starts_with_compdef_tag() -> None:
    assert _completions.zsh_script().startswith("#compdef solx\n")


def test_zsh_dual_mode_footer() -> None:
    """The zsh script supports both install modes: autoloaded from fpath
    (the `loadautofunc` branch calls the completer, so the first Tab of a
    session completes) and eval/source (compdef registers it)."""
    script = _completions.zsh_script()
    assert "if [[ $zsh_eval_context[-1] == loadautofunc ]]; then" in script
    assert '_solx "$@"' in script
    assert "compdef _solx solx" in script
    assert script.rstrip().endswith("fi")


def test_zsh_no_bare_compdef() -> None:
    """A column-0 compdef would register-only on autoload installs, leaving
    the first Tab of a session empty; the call must stay inside the guard."""
    for line in _completions.zsh_script().splitlines():
        assert not line.startswith("compdef")


def test_zsh_path_flags_complete_files() -> None:
    script = _completions.zsh_script()
    assert "_files" in script
    assert "--csv-dir" in script
    assert "--solkeep" in script


def test_bash_registers_completer() -> None:
    script = _completions.bash_script()
    assert "_solx()" in script
    assert "complete -F _solx solx" in script
    assert "COMP_WORDS" in script
    assert "COMP_CWORD" in script


def test_fish_uses_complete_lines() -> None:
    script = _completions.fish_script()
    assert "complete -c solx" in script
    assert "__fish_use_subcommand" in script
    assert "__fish_seen_subcommand_from" in script


@pytest.mark.parametrize("shell", sorted(SCRIPTS))
def test_all_commands_listed(shell: str) -> None:
    script = SCRIPTS[shell]()
    for cmd in TOP_COMMANDS:
        assert cmd in script, f"{shell} script misses top-level command {cmd!r}"
    for sub in JOB_SUBCOMMANDS:
        assert sub in script, f"{shell} script misses job subcommand {sub!r}"
    for sub in CONFIG_SUBCOMMANDS:
        assert sub in script, f"{shell} script misses config subcommand {sub!r}"


# ---- shell syntax checks --------------------------------------------------


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_zsh_syntax(tmp_path) -> None:
    f = tmp_path / "_solx"
    f.write_text(_completions.zsh_script())
    subprocess.run(["zsh", "-n", str(f)], check=True)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_bash_syntax(tmp_path) -> None:
    f = tmp_path / "solx.bash"
    f.write_text(_completions.bash_script())
    subprocess.run(["bash", "-n", str(f)], check=True)


@pytest.mark.skipif(shutil.which("fish") is None, reason="fish not installed")
def test_fish_syntax(tmp_path) -> None:
    f = tmp_path / "solx.fish"
    f.write_text(_completions.fish_script())
    subprocess.run(["fish", "--no-execute", str(f)], check=True)

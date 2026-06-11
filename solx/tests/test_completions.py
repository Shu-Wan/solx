"""Shape and syntax coverage for the static completion scripts.

The scripts are fully static (no callback into solx at completion time), so
the tests assert on their text — every command listed, the right registration
footer per shell — pin the `COMMANDS` table to `main.py`'s argparse tree, and,
where the shell is installed, run its syntax checker over the emitted script
plus functional probes of the bash completer (simulated COMP_WORDS).
"""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess

import pytest

from solx import _completions
from solx import main as main_mod

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


# ---- COMMANDS table pinned to the argparse tree ---------------------------


def _subparsers_action(parser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _optional_forms(parser) -> list[tuple[str, ...]]:
    """Option-string tuples of every optional except the automatic -h/--help."""
    return [
        tuple(a.option_strings)
        for a in parser._actions
        if a.option_strings and tuple(a.option_strings) != ("-h", "--help")
    ]


def _positional_dests(parser) -> list[str]:
    """Completable positionals: skip subparser actions and REMAINDER tails."""
    return [
        a.dest
        for a in parser._actions
        if not a.option_strings
        and not isinstance(a, argparse._SubParsersAction)
        and a.nargs != argparse.REMAINDER
    ]


def _assert_leaf_matches(parser, spec: dict, label: str) -> None:
    forms = [tuple(f[0]) for f in spec.get("flags", [])]
    assert _optional_forms(parser) == forms, f"{label}: flags drifted"
    pos = spec.get("positional")
    dests = _positional_dests(parser)
    if pos is None:
        assert dests == [], f"{label}: parser has a positional COMMANDS misses"
    else:
        assert dests == [pos[0]], f"{label}: positional drifted"


def test_commands_table_pins_parser_tree() -> None:
    """COMMANDS is a hand-written mirror of `main._build_parser()`; walk the
    argparse tree and assert the two agree exactly, so neither the parser nor
    the completion scripts can drift without failing here."""
    parser, _start = main_mod._build_parser()
    root_sub = _subparsers_action(parser)
    assert root_sub is not None
    assert list(root_sub.choices) == list(_completions.COMMANDS)

    expected_root = [
        tuple(f[0]) for f in _completions.GLOBAL_FLAGS if tuple(f[0]) != ("-h", "--help")
    ]
    assert _optional_forms(parser) == expected_root

    for name, spec in _completions.COMMANDS.items():
        p = root_sub.choices[name]
        sub_action = _subparsers_action(p)
        if "sub" in spec:
            assert sub_action is not None, f"{name}: parser is a leaf, table a group"
            assert list(sub_action.choices) == list(spec["sub"]), name
            assert _optional_forms(p) == [], f"{name}: group grew flags"
            for sname, sspec in spec["sub"].items():
                _assert_leaf_matches(sub_action.choices[sname], sspec, f"{name} {sname}")
        else:
            assert sub_action is None, f"{name}: parser is a group, table a leaf"
            _assert_leaf_matches(p, spec, name)


def test_stage_choices_pin_keep_module() -> None:
    """STAGE_CHOICES mirrors what `solx keep --stage` accepts."""
    from solx import keep

    assert _completions.STAGE_CHOICES == ("all", *keep.STAGE_ORDER)


def test_shell_choices_pin_dispatcher() -> None:
    """SHELL_CHOICES mirrors what `solx completions` accepts and renders."""
    assert _completions.SHELL_CHOICES == ("bash", "zsh", "fish")
    assert set(_completions.SHELL_CHOICES) == set(SCRIPTS)


# ---- group-level and re-offer behavior (script text) -----------------------


def test_zsh_groups_offer_help_flags() -> None:
    """`solx job -<Tab>` / `solx config -<Tab>` offer -h/--help."""
    script = _completions.zsh_script()
    help_spec = "'(-h --help)'{-h,--help}'[Show this help message and exit.]'"
    for fn in ("_solx_job()", "_solx_config()"):
        body = script.split(fn, 1)[1].split("\n}", 1)[0]
        assert help_spec in body, f"{fn} lacks a group-level help spec"


def test_fish_groups_offer_help_flags() -> None:
    script = _completions.fish_script()
    assert (
        "complete -c solx -n '__fish_seen_subcommand_from job jobs; "
        "and not __fish_seen_subcommand_from list start stop jump time' "
        "-s h -l help" in script
    )
    assert (
        "complete -c solx -n '__fish_seen_subcommand_from config; "
        "and not __fish_seen_subcommand_from show edit import-solkeep' "
        "-s h -l help" in script
    )


def test_fish_leaves_offer_help_flags() -> None:
    script = _completions.fish_script()
    assert "complete -c solx -n '__fish_seen_subcommand_from keep' -s h -l help" in script


def test_fish_does_not_reoffer_completions_shell() -> None:
    """After `solx completions bash`, the shell names are not offered again."""
    script = _completions.fish_script()
    assert (
        "-n '__fish_seen_subcommand_from completions; "
        "and not __fish_seen_subcommand_from bash zsh fish' -a 'bash zsh fish'"
        in script
    )


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


# ---- functional bash probes (simulated COMP_WORDS) -------------------------

bash_required = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not installed"
)


def _bash_compreply(
    tmp_path,
    words: list[str],
    *,
    line: str | None = None,
    point: int | None = None,
    cwd: str | None = None,
) -> list[str]:
    """Source the bash script, call `_solx` under a simulated completion
    context, and return COMPREPLY one candidate per element."""
    script = tmp_path / "solx.bash"
    script.write_text(_completions.bash_script())
    if line is None:
        line = " ".join(words)
    if point is None:
        point = len(line)
    quoted_words = " ".join(shlex.quote(w) for w in words)
    probe = "\n".join(
        [
            f"source {shlex.quote(str(script))}",
            f"cd {shlex.quote(cwd)}" if cwd else ":",
            f"COMP_WORDS=({quoted_words})",
            f"COMP_CWORD={len(words) - 1}",
            f"COMP_LINE={shlex.quote(line)}",
            f"COMP_POINT={point}",
            "_solx",
            'for r in "${COMPREPLY[@]}"; do printf "%s\\n" "$r"; done',
        ]
    )
    res = subprocess.run(
        ["bash", "-c", probe], capture_output=True, text=True, check=True
    )
    return res.stdout.splitlines()


@bash_required
def test_bash_solkeep_completes_path_with_spaces(tmp_path) -> None:
    """A path containing spaces is one candidate, not one per word."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "my keep list.txt").write_text("")
    reply = _bash_compreply(
        tmp_path, ["solx", "keep", "--solkeep", "my"], cwd=str(files)
    )
    assert reply == ["my keep list.txt"]


@bash_required
def test_bash_solkeep_candidates_stay_literal(tmp_path) -> None:
    """Candidates containing glob characters are not expanded against the cwd."""
    files = tmp_path / "files"
    files.mkdir()
    (files / "a*b").write_text("")
    (files / "axxb").write_text("")
    reply = _bash_compreply(tmp_path, ["solx", "keep", "--solkeep", "a"], cwd=str(files))
    assert sorted(reply) == ["a*b", "axxb"]


@bash_required
def test_bash_midword_completion_uses_cursor_prefix(tmp_path) -> None:
    """Tab in the middle of `jox` (cursor after `jo`) completes `job`."""
    reply = _bash_compreply(
        tmp_path, ["solx", "jox"], line="solx jox", point=len("solx jo")
    )
    assert reply == ["job"]


@bash_required
def test_bash_group_offers_help_flags(tmp_path) -> None:
    assert _bash_compreply(tmp_path, ["solx", "job", "-"]) == ["-h", "--help"]


@bash_required
def test_bash_completions_offers_shells_once(tmp_path) -> None:
    line = "solx completions "
    assert _bash_compreply(
        tmp_path, ["solx", "completions", ""], line=line
    ) == ["bash", "zsh", "fish"]
    line = "solx completions bash "
    assert _bash_compreply(tmp_path, ["solx", "completions", "bash", ""], line=line) == []

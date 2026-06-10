"""Static shell completion scripts for solx (bash, zsh, fish).

One data structure (`COMMANDS`) mirrors the CLI surface: its commands,
subcommands, and flags correspond one-to-one to `main.py`'s parser tree
(a pinning test in `tests/test_completions.py` walks both and fails on any
drift), while the descriptions are one-line summaries of the parser's help
strings. Each `*_script()` function renders the table into a fully static
script: nothing shells back into solx at completion time, so the first Tab
of a session costs no interpreter start.

The zsh script works in both install modes: eval/source (`compdef` registers
the completer) and fpath autoload (`solx completions zsh > ~/.zfunc/_solx`,
where compinit loads the file body *as* the completer, so it must call
itself). A footer keyed on `$zsh_eval_context` picks the right branch.
"""
from __future__ import annotations

# Flag value kinds: None (boolean), "file"/"dir" (filesystem paths), "value"
# (free-form argument), or a tuple of literal choices.
Flag = tuple[tuple[str, ...], "str | tuple[str, ...] | None", str]

_JSON: Flag = (("--json",), None, "Force JSON output (machine-readable).")
HELP_FLAG: Flag = (("-h", "--help"), None, "Show this help message and exit.")

STAGE_CHOICES = ("all", "pending", "over90", "inactive")
SHELL_CHOICES = ("bash", "zsh", "fish")

GLOBAL_FLAGS: list[Flag] = [
    HELP_FLAG,
    (("--version",), None, "Show version and exit."),
    _JSON,
]

# command -> {"help": str, "flags": [Flag], "positional": (label, choices|None),
#             "sub": {subcommand -> same shape}}
COMMANDS: dict[str, dict] = {
    "init": {
        "help": "Write a starter config.toml.",
        "flags": [
            (("-f", "--force", "-y", "--yes"), None, "Overwrite without prompting."),
            _JSON,
        ],
    },
    "keep": {
        "help": "Renew CSV-flagged scratch files filtered by the keep block in config.",
        "flags": [
            (("--stage",), STAGE_CHOICES, "Which warning CSVs to read."),
            (("--csv-dir",), "dir", "Directory holding Sol's warning CSVs."),
            (("--solkeep",), "file", "Path to a gitignore-style keep-list."),
            (("-j", "--jobs"), "value", "Parallel touch workers."),
            (("-y", "--yes", "-f", "--force"), None, "Skip confirmation prompt."),
            (("-n", "--dry-run"), None, "Print plan without executing."),
            (("-v", "--verbose"), None, "Verbose plan + progress."),
            _JSON,
        ],
    },
    "jump": {
        "help": "Drop into a shell on the job's compute node (= solx job jump).",
        "positional": ("jobid", None),
        "flags": [
            (("-q", "--quiet"), None, "Suppress the nesting / most-recent heads-up."),
            _JSON,
        ],
    },
    "job": {
        "help": "Manage interactive Slurm jobs on Sol (alias: jobs).",
        "sub": {
            "list": {
                "help": "Print my Sol jobs.",
                "flags": [_JSON],
            },
            "start": {
                "help": "Start an interactive allocation from a config template.",
                "positional": ("template", None),
                "flags": [
                    (("-n", "--dry-run"), None, "Print salloc argv without submitting."),
                    (("--timeout",), "value", 'Override start_timeout (e.g. "5m", "1h").'),
                ],
            },
            "stop": {
                "help": "Cancel a job (prompts unless -y).",
                "positional": ("jobid", None),
                "flags": [
                    (("-y", "--yes", "-f", "--force"), None, "Skip confirmation prompt."),
                    (("-n", "--dry-run"), None, "Print scancel argv without executing."),
                    _JSON,
                ],
            },
            "jump": {
                "help": "Drop into a shell on the job's compute node.",
                "positional": ("jobid", None),
                "flags": [
                    (("-q", "--quiet"), None, "Suppress the nesting / most-recent heads-up."),
                    _JSON,
                ],
            },
            "time": {
                "help": "Print remaining time (D-HH:MM:SS).",
                "positional": ("jobid", None),
                "flags": [_JSON],
            },
        },
    },
    "config": {
        "help": "Inspect and edit the solx config.",
        "sub": {
            "show": {
                "help": "Print the resolved config.",
                "flags": [(("--json",), None, "Emit JSON.")],
            },
            "edit": {
                "help": "Open the config in $EDITOR.",
                "flags": [],
            },
            "import-solkeep": {
                "help": "Migrate a legacy ~/.solkeep keep-list into the config's keep block.",
                "flags": [
                    (("--solkeep",), "file", "Keep-list to import (default: ~/.solkeep)."),
                    (("-f", "--force"), None, "Accept a lossy import."),
                    _JSON,
                ],
            },
        },
    },
    "completions": {
        "help": "Emit a shell completion script (bash, zsh, or fish).",
        "positional": ("shell", SHELL_CHOICES),
        "flags": [],
    },
    "version": {
        "help": "Show version and exit (alias of --version).",
        "flags": [],
    },
    "help": {
        "help": "Show help and exit (alias of --help).",
        "flags": [],
    },
}


def _flag_words(flags: list[Flag]) -> list[str]:
    return [form for forms, _value, _help in flags for form in forms]


# --- bash --------------------------------------------------------------------


def bash_script() -> str:
    top = " ".join(COMMANDS)
    group_arms: list[str] = []
    leaf_arms: list[str] = []
    for name, spec in COMMANDS.items():
        if "sub" in spec:
            subs = spec["sub"]
            sub_arms = "\n".join(
                f'                {sname}) flags="{" ".join([*_flag_words(sspec.get("flags", [])), "-h", "--help"])}" ;;'
                for sname, sspec in subs.items()
            )
            pattern = f"{name}|jobs" if name == "job" else name
            group_arms.append(
                f"""        {pattern})
            if [[ -z "$sub" ]]; then
                if [[ "$cur" != -* ]]; then
                    mapfile -t COMPREPLY < <(compgen -W "{" ".join(subs)}" -- "$cur")
                    return
                fi
                flags="-h --help"
            fi
            case "$sub" in
{sub_arms}
            esac
            ;;"""
            )
        else:
            words = _flag_words(spec.get("flags", []))
            pos = spec.get("positional")
            choices = ""
            if pos and isinstance(pos[1], tuple):
                choices = " ".join(pos[1])
            leaf_arms.append(
                f"""        {name})
            flags="{" ".join([*words, "-h", "--help"])}"
            words="{choices}"
            ;;"""
            )
    arms = "\n".join(leaf_arms + group_arms)
    return f"""\
# bash completion for solx
_solx() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # On a mid-word Tab, COMP_WORDS carries the whole word; complete against
    # only the part left of the cursor.
    if [[ -n "${{COMP_LINE-}}" ]]; then
        local left="${{COMP_LINE:0:COMP_POINT}}"
        while [[ -n "$cur" && "${{left%"$cur"}}" == "$left" ]]; do
            cur="${{cur%?}}"
        done
    fi

    # First two non-flag words decide the (sub)command context.
    local i word cmd="" sub=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        word="${{COMP_WORDS[i]}}"
        [[ "$word" == -* ]] && continue
        if [[ -z "$cmd" ]]; then
            cmd="$word"
        elif [[ -z "$sub" ]]; then
            sub="$word"
        fi
    done

    # Option values. Path candidates go through mapfile (no word splitting,
    # no glob expansion — spaces and metacharacters survive) and `compopt -o
    # filenames` (where available) so readline escapes what it inserts.
    case "$prev" in
        --csv-dir)
            type compopt &> /dev/null && compopt -o filenames 2> /dev/null
            mapfile -t COMPREPLY < <(compgen -d -- "$cur")
            return
            ;;
        --solkeep)
            type compopt &> /dev/null && compopt -o filenames 2> /dev/null
            mapfile -t COMPREPLY < <(compgen -f -- "$cur")
            return
            ;;
        --stage)
            mapfile -t COMPREPLY < <(compgen -W "{" ".join(STAGE_CHOICES)}" -- "$cur")
            return
            ;;
        -j|--jobs|--timeout)
            return
            ;;
    esac

    if [[ -z "$cmd" ]]; then
        if [[ "$cur" == -* ]]; then
            mapfile -t COMPREPLY < <(compgen -W "{" ".join(_flag_words(GLOBAL_FLAGS))}" -- "$cur")
        else
            mapfile -t COMPREPLY < <(compgen -W "{top}" -- "$cur")
        fi
        return
    fi

    local flags="" words=""
    case "$cmd" in
{arms}
    esac
    if [[ "$cur" == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$flags" -- "$cur")
    elif [[ -n "$words" && -z "$sub" ]]; then
        # $words holds positional choices; offer them only until the
        # positional is filled.
        mapfile -t COMPREPLY < <(compgen -W "$words" -- "$cur")
    fi
}}

complete -F _solx solx"""


# --- zsh ---------------------------------------------------------------------


def _zsh_q(text: str) -> str:
    """Quote `text` for inclusion inside a zsh single-quoted string."""
    return text.replace("'", "'\\''")


def _zsh_desc(text: str) -> str:
    """Sanitize a description for an `_arguments` `[...]` field."""
    return _zsh_q(text.replace("[", "").replace("]", ""))


def _zsh_item(name: str, desc: str) -> str:
    """Render one `name:description` element for `_describe`."""
    escaped = _zsh_q(desc.replace(":", "\\:"))
    return f"'{name}:{escaped}'"


def _zsh_flag_specs(flags: list[Flag]) -> list[str]:
    specs: list[str] = []
    for forms, value, help_text in flags:
        action = ""
        if value == "file":
            action = ":file:_files"
        elif value == "dir":
            action = ":directory:_files -/"
        elif value == "value":
            action = ":value:"
        elif isinstance(value, tuple):
            action = f":value:({' '.join(value)})"
        desc = f"[{_zsh_desc(help_text)}]"
        if len(forms) == 1:
            specs.append(f"'{forms[0]}{desc}{action}'")
        else:
            exclusion = " ".join(forms)
            brace = ",".join(forms)
            specs.append(f"'({exclusion})'{{{brace}}}'{desc}{action}'")
    return specs


def _zsh_leaf_arguments(spec: dict, indent: str) -> str:
    """Render the `_arguments` call for a leaf (sub)command."""
    parts = _zsh_flag_specs(spec.get("flags", []))
    parts.append("'(-h --help)'{-h,--help}'[Show this help message and exit.]'")
    pos = spec.get("positional")
    if pos is not None:
        label, choices = pos
        action = f"({' '.join(choices)})" if isinstance(choices, tuple) else ""
        parts.append(f"'1:{label}:{action}'")
    joined = f" \\\n{indent}    ".join(parts)
    return f"{indent}_arguments \\\n{indent}    {joined}"


def _zsh_group_fn(name: str, spec: dict) -> str:
    subs = spec["sub"]
    items = "\n                ".join(
        _zsh_item(sname, sspec["help"]) for sname, sspec in subs.items()
    )
    arms = []
    for sname, sspec in subs.items():
        arms.append(
            f"                ({sname})\n"
            + _zsh_leaf_arguments(sspec, "                    ")
            + "\n                    ;;"
        )
    arms_text = "\n".join(arms)
    return f"""\
_solx_{name}() {{
    local curcontext="$curcontext" state line
    typeset -A opt_args

    _arguments -C \\
        '(-h --help)'{{-h,--help}}'[Show this help message and exit.]' \\
        '1: :->subcommand' \\
        '*:: :->subargs'

    case $state in
        (subcommand)
            local -a subcommands
            subcommands=(
                {items}
            )
            _describe -t commands 'solx {name} command' subcommands
            ;;
        (subargs)
            case $words[1] in
{arms_text}
            esac
            ;;
    esac
}}"""


def zsh_script() -> str:
    group_fns = [
        _zsh_group_fn(name, spec) for name, spec in COMMANDS.items() if "sub" in spec
    ]
    items = "\n                ".join(
        _zsh_item(name, spec["help"]) for name, spec in COMMANDS.items()
    )
    arms = []
    for name, spec in COMMANDS.items():
        if "sub" in spec:
            pattern = f"({name}|jobs)" if name == "job" else f"({name})"
            arms.append(f"                {pattern}\n                    _solx_{name}\n                    ;;")
        else:
            arms.append(
                f"                ({name})\n"
                + _zsh_leaf_arguments(spec, "                    ")
                + "\n                    ;;"
            )
    arms_text = "\n".join(arms)
    group_fns_text = "\n\n".join(group_fns)
    body = f"""\
#compdef solx

{group_fns_text}

_solx() {{
    local curcontext="$curcontext" state line
    typeset -A opt_args

    _arguments -C \\
        '(-h --help)'{{-h,--help}}'[Show this help message and exit.]' \\
        '--version[Show version and exit.]' \\
        '--json[Force JSON output (machine-readable).]' \\
        '1: :->command' \\
        '*:: :->args'

    case $state in
        (command)
            local -a commands
            commands=(
                {items}
            )
            _describe -t commands 'solx command' commands
            ;;
        (args)
            case $words[1] in
{arms_text}
            esac
            ;;
    esac
}}

if [[ $zsh_eval_context[-1] == loadautofunc ]]; then
    # autoload from fpath, call function directly
    _solx "$@"
else
    # eval/source/. command, register function for later
    compdef _solx solx
fi"""
    return body


# --- fish --------------------------------------------------------------------


def _fish_q(text: str) -> str:
    """Quote `text` for a fish single-quoted string."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _fish_flag_lines(flags: list[Flag], condition: str) -> list[str]:
    lines: list[str] = []
    for forms, value, help_text in flags:
        opts = " ".join(
            f"-s {form.lstrip('-')}" if not form.startswith("--") else f"-l {form[2:]}"
            for form in forms
        )
        extra = ""
        if value in ("file", "dir"):
            extra = " -r -F"
        elif value == "value":
            extra = " -x"
        elif isinstance(value, tuple):
            extra = f" -x -a '{' '.join(value)}'"
        lines.append(
            f"complete -c solx -n '{condition}' {opts}{extra} -d '{_fish_q(help_text)}'"
        )
    return lines


def fish_script() -> str:
    lines = [
        "# fish completion for solx",
        "complete -c solx -f",
    ]
    for forms, _value, help_text in GLOBAL_FLAGS:
        opts = " ".join(
            f"-s {form.lstrip('-')}" if not form.startswith("--") else f"-l {form[2:]}"
            for form in forms
        )
        lines.append(
            f"complete -c solx -n __fish_use_subcommand {opts} -d '{_fish_q(help_text)}'"
        )
    for name, spec in COMMANDS.items():
        lines.append(
            f"complete -c solx -n __fish_use_subcommand -a {name} -d '{_fish_q(spec['help'])}'"
        )
        if "sub" in spec:
            seen = f"__fish_seen_subcommand_from {name}"
            if name == "job":
                seen = "__fish_seen_subcommand_from job jobs"
            subnames = " ".join(spec["sub"])
            # Group level (no subcommand picked yet): only -h/--help.
            lines.extend(
                _fish_flag_lines(
                    [HELP_FLAG],
                    f"{seen}; and not __fish_seen_subcommand_from {subnames}",
                )
            )
            for sname, sspec in spec["sub"].items():
                lines.append(
                    f"complete -c solx -n '{seen}; and not __fish_seen_subcommand_from {subnames}' "
                    f"-a {sname} -d '{_fish_q(sspec['help'])}'"
                )
                lines.extend(
                    _fish_flag_lines(
                        [*sspec.get("flags", []), HELP_FLAG],
                        f"{seen}; and __fish_seen_subcommand_from {sname}",
                    )
                )
        else:
            condition = f"__fish_seen_subcommand_from {name}"
            lines.extend(
                _fish_flag_lines([*spec.get("flags", []), HELP_FLAG], condition)
            )
            pos = spec.get("positional")
            if pos is not None and isinstance(pos[1], tuple):
                choices = " ".join(pos[1])
                # Offer the positional's choices only until one is given.
                lines.append(
                    f"complete -c solx "
                    f"-n '{condition}; and not __fish_seen_subcommand_from {choices}' "
                    f"-a '{choices}'"
                )
    return "\n".join(lines)

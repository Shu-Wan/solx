"""Command-line entry point for `solx`.

Surface (see docs/solx.md):

    solx init
    solx job list  (alias `ls`; group also reachable as `jobs`)
    solx job start [TEMPLATE]
    solx job stop  [JOBID]
    solx job jump  [JOBID] [-q]   (also `solx jump`)
    solx job time  [JOBID]
    solx keep      [--stage S] [--csv-dir D] [-j N] [-y] [-n] [-v]
    solx config show [--json]
    solx config edit
    solx config import-solkeep   (migrate ~/.solkeep into [keep])
    solx completions <bash|zsh|fish>
    solx version   (alias of --version)
    solx help      (alias of --help)

Global output flag: `--json` forces JSON; by default output auto-detects
(Rich tables on a terminal, JSON when stdout is not a TTY). See `solx.output`.
Every output-producing leaf subcommand also accepts a trailing `--json`.
After `job start`, a `--json` belongs to the salloc passthrough; `config
edit`, `completions`, `version`, and `help` take no `--json` at all.
"""
from __future__ import annotations

import os
import sys

from solx import __version__

# Recognized by type checkers like typing.TYPE_CHECKING, without importing
# `typing` at runtime.
TYPE_CHECKING = False
if TYPE_CHECKING:
    import argparse

    from solx.output import Out

# solx's home lives on NFS, where every module import is a network round-trip,
# so every invocation pays for whatever this module pulls in. Importing this
# module loads nothing beyond what the interpreter already has: argparse and
# pathlib are imported when the parser tree is built (so the `--version` /
# `version` fast path in `main()` skips them entirely), and command
# implementations (with their rich/pathspec dependency trees) are imported
# inside the handlers below.

_JSON_HELP = "Force JSON output (machine-readable)."


# --- helpers ----------------------------------------------------------------


def _require_sol() -> None:
    from solx.side import require_sol

    require_sol()


def _out(json_flag: bool) -> Out:
    """Build the resolved output target for a command body."""
    from solx.output import Out

    return Out.auto(force="json" if json_flag else None)


def _json_flag(ns: argparse.Namespace) -> bool:
    """Resolved --json: the root flag or the subcommand's trailing flag."""
    return bool(getattr(ns, "json_root", False) or getattr(ns, "json_leaf", False))


def _load_or_exit(out: Out):
    from solx import config as cfg
    from solx.config import ConfigError

    try:
        return cfg.load()
    except ConfigError as e:
        out.error(f"error: {e}")
        raise SystemExit(2)


# --- command handlers -------------------------------------------------------


def _cmd_init(ns: argparse.Namespace) -> None:
    _require_sol()
    from pathlib import Path

    from solx import init as init_mod

    # Auto-import an existing ~/.solkeep into the new config's [keep] block.
    sys.exit(
        init_mod.cmd_init(
            force=ns.force, solkeep=Path.home() / ".solkeep", out=_out(_json_flag(ns))
        )
    )


def _cmd_keep(ns: argparse.Namespace) -> None:
    _require_sol()
    from solx import config as cfg
    from solx import keep as keep_mod

    out = _out(_json_flag(ns))
    valid_stages = {"all", *keep_mod.STAGE_ORDER}
    if ns.stage not in valid_stages:
        out.error(
            f"invalid --stage {ns.stage!r}. choose from: {', '.join(sorted(valid_stages))}"
        )
        sys.exit(2)
    if ns.jobs_n < 1:
        out.error(f"invalid --jobs {ns.jobs_n}. must be >= 1.")
        sys.exit(2)
    # `keep` can run off a `~/.solkeep` alone, so a missing config.toml is fine
    # (config stays None). A config that exists but is malformed still errors.
    config = _load_or_exit(out) if cfg.config_path().exists() else None
    sys.exit(
        keep_mod.cmd_keep(
            config=config,
            csv_dir=ns.csv_dir,
            stage=ns.stage,
            jobs_n=ns.jobs_n,
            yes=ns.yes,
            dry_run=ns.dry_run,
            verbose=ns.verbose,
            solkeep=ns.solkeep,
            out=out,
        )
    )


def _cmd_jump(ns: argparse.Namespace) -> None:
    _require_sol()
    from solx import jobs as jobs_mod

    out = _out(_json_flag(ns))
    config = _load_or_exit(out)
    sys.exit(
        jobs_mod.cmd_jump(config=config, jobid_arg=ns.jobid, quiet=ns.quiet, out=out)
    )


def _cmd_job_list(ns: argparse.Namespace) -> None:
    _require_sol()
    from solx import jobs as jobs_mod

    sys.exit(jobs_mod.cmd_list(out=_out(_json_flag(ns))))


def _cmd_job_stop(ns: argparse.Namespace) -> None:
    _require_sol()
    from solx import jobs as jobs_mod

    sys.exit(
        jobs_mod.cmd_stop(
            jobid_arg=ns.jobid, yes=ns.yes, dry_run=ns.dry_run, out=_out(_json_flag(ns))
        )
    )


def _cmd_job_time(ns: argparse.Namespace) -> None:
    _require_sol()
    from solx import jobs as jobs_mod

    sys.exit(jobs_mod.cmd_time(jobid_arg=ns.jobid, out=_out(_json_flag(ns))))


# Short flags `job start` recognizes ahead of `--`. A bundle of short flags
# (`-nn`) is consumed only when every letter is in this set.
_START_SHORTS = frozenset("n")


def _run_job_start(
    json_flag: bool,
    tail: list[str],
    help_parser: argparse.ArgumentParser | None = None,
) -> None:
    """Parse the `job start` tail and run the command.

    `job start` forwards unrecognized tokens to salloc, so its tail is parsed
    here rather than by argparse:

    * Ahead of `--`: `-n`/`--dry-run` and `--timeout VALUE` (or
      `--timeout=VALUE`) are consumed wherever they appear, even interleaved
      with passthrough; a bundle of short flags (`-nn` == `-n -n`) is consumed
      when every letter is a recognized short flag and forwarded whole to
      salloc otherwise.
    * The first `--` is consumed and shields everything after it: no later
      token is ever parsed as a flag, and later `--` tokens are forwarded
      literally.
    * The first token not consumed by a known option names the TEMPLATE — on
      either side of `--`.
    * Every other token is passthrough to salloc, in its original order.
    """
    _require_sol()
    dry_run = False
    timeout: str | None = None
    template: str | None = None
    passthrough: list[str] = []
    dd_seen = False
    i = 0
    while i < len(tail):
        tok = tail[i]
        if dd_seen:
            if template is None:
                template = tok
            else:
                passthrough.append(tok)
        elif tok == "--":
            dd_seen = True
        elif tok in ("-n", "--dry-run"):
            dry_run = True
        elif tok.startswith("--dry-run="):
            print("error: option --dry-run does not take a value", file=sys.stderr)
            sys.exit(2)
        elif tok == "--timeout":
            if i + 1 >= len(tail):
                print("error: option --timeout requires an argument", file=sys.stderr)
                sys.exit(2)
            i += 1
            timeout = tail[i]
        elif tok.startswith("--timeout="):
            timeout = tok[len("--timeout=") :]
        elif tok in ("-h", "--help") and help_parser is not None:
            help_parser.print_help()
            sys.exit(0)
        elif len(tok) > 2 and tok[0] == "-" and all("a" <= c <= "z" for c in tok[1:]):
            if all(c in _START_SHORTS for c in tok[1:]):
                # Every letter is a known short flag — and `n` is the only
                # one, so the bundle is some number of `-n` repeats.
                dry_run = True
            else:
                passthrough.append(tok)
        elif template is None:
            template = tok
        else:
            passthrough.append(tok)
        i += 1

    from solx import config as cfg
    from solx import jobs as jobs_mod
    from solx.config import ConfigError

    out = _out(json_flag)
    config = _load_or_exit(out)
    timeout_seconds: int | None = None
    if timeout:
        try:
            timeout_seconds = cfg.parse_duration(timeout)
        except ConfigError as e:
            out.error(f"error: {e}")
            sys.exit(2)
    sys.exit(
        jobs_mod.cmd_start(
            config=config,
            template_name=template,
            dry_run=dry_run,
            timeout_override=timeout_seconds,
            passthrough=passthrough,
            out=out,
        )
    )


def _cmd_job_start_parsed(ns: argparse.Namespace) -> None:
    # `main()` hands every `job start` invocation to `_run_job_start` before
    # argparse dispatch; this reconstructs the tail for any stray path that
    # still lands on the subparser, so both routes share one implementation.
    tail: list[str] = []
    if ns.dry_run:
        tail.append("-n")
    if ns.timeout is not None:
        tail.extend(["--timeout", ns.timeout])
    if ns.template is not None:
        tail.append(ns.template)
    tail.extend(ns.args)
    _run_job_start(_json_flag(ns), tail)


def _cmd_config_show(ns: argparse.Namespace) -> None:
    _require_sol()
    out = _out(bool(getattr(ns, "json_root", False)))
    config = _load_or_exit(out)
    as_json = bool(ns.json_leaf) or out.json_mode

    if as_json:
        from dataclasses import asdict

        # KeepRules holds compiled pathspec objects; serialize raw inputs only.
        data = {
            "default_shell": config.default_shell,
            "default_template": config.default_template,
            "start_timeout_seconds": config.start_timeout_seconds,
            "templates": {
                name: {k: v for k, v in asdict(t).items() if v not in (None, ())}
                for name, t in config.templates.items()
            },
            "keep": (
                {
                    "include": list(config.keep.raw_include),
                    "exclude": list(config.keep.raw_exclude),
                }
                if config.keep is not None
                else None
            ),
        }
        out.json(data)
        sys.exit(0)

    from rich.table import Table

    c = out.stdout
    c.print(f"[bold]default_shell[/]    {config.default_shell}")
    c.print(f"[bold]default_template[/] {config.default_template}")
    c.print(f"[bold]start_timeout[/]    {config.start_timeout_seconds}s")

    for name, t in config.templates.items():
        tbl = Table(title=rf"\[jobs.{name}]", show_header=False, title_justify="left")
        tbl.add_row("partition", t.partition)
        tbl.add_row("time", t.time)
        if t.qos:
            tbl.add_row("qos", t.qos)
        if t.gres:
            tbl.add_row("gres", t.gres)
        if t.extra_args:
            tbl.add_row("extra_args", " ".join(t.extra_args))
        c.print(tbl)

    if config.keep is not None:
        tbl = Table(title=r"\[keep]", show_header=False, title_justify="left")
        tbl.add_row("include", "\n".join(config.keep.raw_include))
        if config.keep.raw_exclude:
            tbl.add_row("exclude", "\n".join(config.keep.raw_exclude))
        c.print(tbl)
    else:
        c.print(r"[dim]\[keep] not configured (solx keep will exit 2)[/]")
    sys.exit(0)


def _cmd_config_edit(ns: argparse.Namespace) -> None:
    _require_sol()
    import shlex
    import shutil
    import subprocess

    from solx import config as cfg

    p = cfg.config_path()
    if not p.exists():
        print(f"no config at {p}. run `solx init` first.", file=sys.stderr)
        sys.exit(2)
    # $EDITOR is often a command with flags (e.g. "code --wait", "vim -u NORC"),
    # so split it into argv rather than treating the whole string as one binary.
    editor = os.environ.get("EDITOR") or shutil.which("vi") or "nano"
    editor_argv = shlex.split(editor)
    sys.exit(subprocess.call([*editor_argv, str(p)]))


def _cmd_config_import_solkeep(ns: argparse.Namespace) -> None:
    _require_sol()
    from solx import init as init_mod

    sys.exit(
        init_mod.cmd_import_solkeep(
            solkeep=ns.solkeep, force=ns.force, out=_out(_json_flag(ns))
        )
    )


def _cmd_completions(ns: argparse.Namespace) -> None:
    shell = ns.shell.lower()
    if shell not in {"bash", "zsh", "fish"}:
        print(f"unknown shell {shell!r}; choose bash, zsh, or fish.", file=sys.stderr)
        sys.exit(2)
    from solx import _completions

    script = {
        "bash": _completions.bash_script,
        "zsh": _completions.zsh_script,
        "fish": _completions.fish_script,
    }[shell]()
    print(script)
    sys.exit(0)


def _cmd_version(ns: argparse.Namespace) -> None:
    print(__version__)
    sys.exit(0)


def _cmd_help(ns: argparse.Namespace) -> None:
    # The root help, matching `solx --help`.
    ns.help_parser.print_help()
    sys.exit(0)


# --- parser tree ------------------------------------------------------------


def _add_json(p: argparse.ArgumentParser, help: str = _JSON_HELP) -> None:
    p.add_argument(
        "--json", action="store_true", dest="json_leaf", default=False, help=help
    )


def _build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """Build the argparse tree; returns (root parser, `job start` subparser).

    Every parser sets ``allow_abbrev=False``: option prefixes are never
    expanded (`--time` must not match `--timeout`).
    """
    import argparse
    from pathlib import Path

    class _VersionAction(argparse.Action):
        """Record `--version`; `main()` prints the version only after the
        whole line parses, so invalid tokens elsewhere still error (exit 2)."""

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, True)

    parser = argparse.ArgumentParser(
        prog="solx",
        description="CLI for ASU's Sol supercomputer.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version",
        action=_VersionAction,
        dest="show_version",
        default=False,
        nargs=0,
        help="Show version and exit.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_root", default=False, help=_JSON_HELP
    )
    parser.set_defaults(func=None, help_parser=parser)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    # -- init
    p = sub.add_parser(
        "init",
        help="Write a starter config.toml.",
        description="Write a starter config.toml.",
        allow_abbrev=False,
    )
    p.add_argument(
        "-f", "--force", "-y", "--yes",
        dest="force",
        action="store_true",
        help="Overwrite without prompting (-y/--yes accepted too).",
    )
    _add_json(p)
    p.set_defaults(func=_cmd_init)

    # -- keep
    p = sub.add_parser(
        "keep",
        help="Renew CSV-flagged scratch files filtered by the keep block in config.",
        description="Renew CSV-flagged scratch files filtered by the keep block in config.",
        allow_abbrev=False,
    )
    p.add_argument("--stage", default="all", help="Which warning CSVs to read.")
    p.add_argument(
        "--csv-dir",
        dest="csv_dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory holding Sol's warning CSVs.",
    )
    p.add_argument(
        "--solkeep",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to a gitignore-style keep-list (overrides the [keep] config block).",
    )
    p.add_argument(
        "-j", "--jobs",
        dest="jobs_n",
        type=int,
        default=max(1, min(8, (os.cpu_count() or 2) // 4)),
        metavar="N",
        help="Parallel touch workers.",
    )
    p.add_argument(
        "-y", "--yes", "-f", "--force",
        dest="yes",
        action="store_true",
        help="Skip confirmation prompt (also -f/--force).",
    )
    p.add_argument(
        "-n", "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Print plan without executing.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose plan + progress."
    )
    _add_json(p)
    p.set_defaults(func=_cmd_keep)

    # -- jump (shortcut for `job jump`)
    p = sub.add_parser(
        "jump",
        help="Drop into a shell on the job's compute node (= solx job jump).",
        description="Drop into a shell on the job's compute node (= solx job jump).",
        allow_abbrev=False,
    )
    p.add_argument(
        "jobid",
        nargs="?",
        default=None,
        help="Job ID. Defaults to current job (compute) or sole/most-recent running job (login).",
    )
    p.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress the nesting / most-recent heads-up.",
    )
    _add_json(p)
    p.set_defaults(func=_cmd_jump)

    # -- job group
    p_job = sub.add_parser(
        "job",
        help="Manage interactive Slurm jobs on Sol (alias: jobs).",
        description="Manage interactive Slurm jobs on Sol (alias: jobs).",
        allow_abbrev=False,
    )
    p_job.set_defaults(func=None, help_parser=p_job)
    job_sub = p_job.add_subparsers(dest="job_command", metavar="COMMAND", title="commands")

    p = job_sub.add_parser(
        "list",
        help="Print my Sol jobs.",
        description="Print my Sol jobs.",
        allow_abbrev=False,
    )
    _add_json(p)
    p.set_defaults(func=_cmd_job_list)

    p_start = job_sub.add_parser(
        "start",
        help="Start an interactive allocation from a config template.",
        description="Start an interactive allocation from a config template.",
        allow_abbrev=False,
    )
    p_start.add_argument(
        "template",
        nargs="?",
        default=None,
        help="Template name; defaults to default_template.",
    )
    p_start.add_argument(
        "-n", "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Print salloc argv without submitting.",
    )
    p_start.add_argument(
        "--timeout",
        default=None,
        metavar="DURATION",
        help='Override start_timeout (e.g. "5m", "1h").',
    )
    p_start.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        metavar="ARGS",
        help="Extra arguments forwarded to salloc.",
    )
    # No --json leaf flag here: after `job start`, --json belongs to the
    # salloc passthrough.
    p_start.set_defaults(func=_cmd_job_start_parsed)

    p = job_sub.add_parser(
        "stop",
        help="Cancel a job (prompts unless -y).",
        description="Cancel a job (prompts unless -y).",
        allow_abbrev=False,
    )
    p.add_argument(
        "jobid", nargs="?", default=None, help="Job ID. Defaults per resolution rules."
    )
    p.add_argument(
        "-y", "--yes", "-f", "--force",
        dest="yes",
        action="store_true",
        help="Skip confirmation prompt (also -f/--force).",
    )
    p.add_argument(
        "-n", "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Print scancel argv without executing.",
    )
    _add_json(p)
    p.set_defaults(func=_cmd_job_stop)

    p = job_sub.add_parser(
        "jump",
        help="Drop into a shell on the job's compute node.",
        description="Drop into a shell on the job's compute node.",
        allow_abbrev=False,
    )
    p.add_argument(
        "jobid", nargs="?", default=None, help="Job ID. Defaults per resolution rules."
    )
    p.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress the nesting / most-recent heads-up.",
    )
    _add_json(p)
    p.set_defaults(func=_cmd_jump)

    p = job_sub.add_parser(
        "time",
        help="Print remaining time (D-HH:MM:SS).",
        description="Print remaining time (D-HH:MM:SS).",
        allow_abbrev=False,
    )
    p.add_argument(
        "jobid", nargs="?", default=None, help="Job ID. Defaults per resolution rules."
    )
    _add_json(p)
    p.set_defaults(func=_cmd_job_time)

    # -- config group
    p_config = sub.add_parser(
        "config",
        help="Inspect and edit the solx config.",
        description="Inspect and edit the solx config.",
        allow_abbrev=False,
    )
    p_config.set_defaults(func=None, help_parser=p_config)
    config_sub = p_config.add_subparsers(
        dest="config_command", metavar="COMMAND", title="commands"
    )

    p = config_sub.add_parser(
        "show",
        help="Print the resolved config.",
        description="Print the resolved config.",
        allow_abbrev=False,
    )
    _add_json(p, help="Emit JSON.")
    p.set_defaults(func=_cmd_config_show)

    p = config_sub.add_parser(
        "edit",
        help="Open the config in $EDITOR.",
        description="Open the config in $EDITOR.",
        allow_abbrev=False,
    )
    p.set_defaults(func=_cmd_config_edit)

    p = config_sub.add_parser(
        "import-solkeep",
        help="Migrate a legacy ~/.solkeep keep-list into the config's [keep] block.",
        description="Migrate a legacy ~/.solkeep keep-list into the config's [keep] block.",
        allow_abbrev=False,
    )
    p.add_argument(
        "--solkeep",
        type=Path,
        default=None,
        metavar="FILE",
        help="Keep-list to import (default: ~/.solkeep).",
    )
    p.add_argument(
        "-f", "--force",
        action="store_true",
        help="Accept a lossy import (an order-dependent re-include that "
        "the [keep] block can't preserve).",
    )
    _add_json(p)
    p.set_defaults(func=_cmd_config_import_solkeep)

    # -- completions
    p = sub.add_parser(
        "completions",
        help="Emit a shell completion script (bash, zsh, or fish).",
        description="Emit a shell completion script (bash, zsh, or fish).",
        allow_abbrev=False,
    )
    p.add_argument("shell", help="Target shell: bash, zsh, or fish.")
    p.set_defaults(func=_cmd_completions)

    # -- meta: version / help (no --json: their output is one fixed text)
    p = sub.add_parser(
        "version",
        help="Show version and exit (alias of --version).",
        description="Show version and exit (alias of --version).",
        allow_abbrev=False,
    )
    p.set_defaults(func=_cmd_version)

    p = sub.add_parser(
        "help",
        help="Show help and exit (alias of --help).",
        description="Show help and exit (alias of --help).",
        allow_abbrev=False,
    )
    p.set_defaults(func=_cmd_help)

    return parser, p_start


# --- entry point -------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    # Completion scripts generated by solx <= 0.4.0 call back into `solx`
    # with _SOLX_COMPLETE set (the Typer runtime-completion protocol). Exit
    # silently so a stale installed script offers zero candidates instead of
    # parsing help text as completions.
    if "_SOLX_COMPLETE" in os.environ:
        raise SystemExit(0)

    args = list(sys.argv[1:] if argv is None else argv)

    # Exactly `solx --version` / `solx version` short-circuits everything
    # else: no Sol check, no parser tree. Any longer argv goes through
    # argparse, so junk around either version form still errors.
    if args == ["--version"] or args == ["version"]:
        print(__version__)
        raise SystemExit(0)

    # Hidden aliases, rewritten before parsing so help stays clean:
    # `solx jobs …` == `solx job …` and `solx job ls` == `solx job list`.
    for i, tok in enumerate(args):
        if tok == "--":
            break
        if tok.startswith("-"):
            continue
        if tok == "jobs":
            args[i] = "job"
        if args[i] == "job" and i + 1 < len(args) and args[i + 1] == "ls":
            args[i + 1] = "list"
        break

    parser, start_parser = _build_parser()

    # `job start` owns its tail (unrecognized tokens are salloc passthrough),
    # so it is dispatched before argparse parses anything. Root options ahead
    # of the subcommand are limited to --json on this path; anything else
    # falls through to argparse for regular help/error handling.
    head: list[str] = []
    k = 0
    while k < len(args) and args[k].startswith("-") and args[k] != "--":
        head.append(args[k])
        k += 1
    if args[k : k + 2] == ["job", "start"] and all(t == "--json" for t in head):
        _run_job_start("--json" in head, args[k + 2 :], start_parser)

    ns = parser.parse_args(args)
    if ns.show_version:
        # `--version` mixed into an otherwise-valid root line wins over any
        # subcommand on it.
        print(__version__)
        raise SystemExit(0)
    if ns.func is None:
        # A group (or the root) given no subcommand: print its help, exit 2.
        ns.help_parser.print_help()
        raise SystemExit(2)
    ns.func(ns)
    raise SystemExit(0)

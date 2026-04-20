"""Sol-side subcommands: session start/info/stop, config init/show.

Every function returns an exit code (0 ok / 1 failure / 2 conditional) and
takes optional injection points (`submit`, `poll`, `scancel`, `sleep`,
`session_path`, `config_path`) so tests can swap subprocess and time
without monkeypatching.
"""
from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

from solx import config as cfg
from solx import session as sess


console = Console()
err_console = Console(stderr=True)


SUPPORTED_KINDS = frozenset({"bare"})

# Polling defaults for `wait_for_running`. Bounded so a stuck queue surfaces
# instead of hanging forever; the user can `scancel` and retry with a
# different profile.
DEFAULT_WAIT_TIMEOUT = 600  # 10 minutes
DEFAULT_POLL_INTERVAL = 2.0


# ---------------------------------------------------------------------------
# Argv construction
# ---------------------------------------------------------------------------


def build_sbatch_argv(
    profile: cfg.Profile,
    *,
    passthrough: list[str] | None = None,
) -> list[str]:
    """Compose the sbatch argv for a `kind=bare` profile.

    Uses `sbatch --parsable --wrap='sleep infinity'` so the allocation
    persists in the background; the user later joins it with `srun
    --jobid=<id> --pty bash` (or via a future `solx session shell`).
    """
    argv: list[str] = [
        "sbatch",
        "--parsable",
        f"--job-name=solx-{profile.name}",
    ]
    if profile.partition:
        argv.append(f"--partition={profile.partition}")
    if profile.qos:
        argv.append(f"--qos={profile.qos}")
    if profile.time:
        argv.append(f"--time={profile.time}")
    if profile.gres:
        argv.append(f"--gres={profile.gres}")
    argv.extend(profile.srun_args)
    if passthrough:
        argv.extend(passthrough)
    argv.append("--wrap=sleep infinity")
    return argv


# ---------------------------------------------------------------------------
# Subprocess runners (default implementations; tests inject substitutes)
# ---------------------------------------------------------------------------


_JOBID_RE = re.compile(r"^(\d+)(?:;.*)?$")


def _default_submit(argv: list[str]) -> str:
    """Run sbatch and return the job_id from its stdout.

    `sbatch --parsable` prints either `<jobid>` or `<jobid>;<cluster>`.
    """
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sbatch failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    line = (result.stdout or "").strip().splitlines()[0] if result.stdout else ""
    match = _JOBID_RE.match(line)
    if not match:
        raise RuntimeError(f"could not parse job id from sbatch output: {line!r}")
    return match.group(1)


def _default_poll(job_id: str) -> tuple[str, str]:
    """Return `(state, node)` from `squeue -j <id>`. Empty state means gone."""
    result = subprocess.run(
        ["squeue", "-h", "-j", job_id, "-o", "%T %N"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    out = (result.stdout or "").strip()
    if not out:
        return ("", "")
    parts = out.split(None, 1)
    state = parts[0]
    node = parts[1] if len(parts) > 1 else ""
    return (state, node)


def _default_scancel(job_id: str) -> None:
    subprocess.run(["scancel", job_id], check=False, timeout=30)


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


def wait_for_running(
    job_id: str,
    *,
    poll: Callable[[str], tuple[str, str]] | None = None,
    sleep: Callable[[float], None] | None = None,
    timeout: int = DEFAULT_WAIT_TIMEOUT,
    interval: float = DEFAULT_POLL_INTERVAL,
    now: Callable[[], float] | None = None,
) -> str:
    """Poll squeue until the job is RUNNING, then return the assigned node."""
    poll_fn = poll or _default_poll
    sleep_fn = sleep or time.sleep
    now_fn = now or time.monotonic

    deadline = now_fn() + timeout
    last_state: str | None = None

    while now_fn() < deadline:
        state, node = poll_fn(job_id)

        if state == "RUNNING" and node:
            return node

        if state in {"FAILED", "CANCELLED", "TIMEOUT", "COMPLETED", "BOOT_FAIL"}:
            raise RuntimeError(
                f"job {job_id} ended in state {state} before reaching RUNNING"
            )

        if state and state != last_state:
            console.print(f"[dim]job {job_id}: {state}[/dim]")
            last_state = state

        sleep_fn(interval)

    raise RuntimeError(
        f"job {job_id} did not start within {timeout}s — try `scancel {job_id}`"
    )


# ---------------------------------------------------------------------------
# session start / info / stop
# ---------------------------------------------------------------------------


def session_start(
    profile_name: str = "default",
    *,
    dry_run: bool = False,
    passthrough: list[str] | None = None,
    config_path: Path | None = None,
    session_path: Path | None = None,
    submit: Callable[[list[str]], str] | None = None,
    poll: Callable[[str], tuple[str, str]] | None = None,
    sleep: Callable[[float], None] | None = None,
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> int:
    try:
        profile = cfg.resolve(profile_name, path=config_path)
    except cfg.ConfigError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        return 2

    if profile.kind not in SUPPORTED_KINDS:
        err_console.print(
            f"[red]error:[/red] kind={profile.kind!r} not supported in this release.\n"
            f"Supported: {', '.join(sorted(SUPPORTED_KINDS))}. "
            f"Edit {config_path or cfg.default_profiles_path()} or pick another profile."
        )
        return 2

    existing = _check_for_existing(session_path)
    if existing == "running":
        return 2
    # "stale" or "absent" → continue.

    argv = build_sbatch_argv(profile, passthrough=passthrough)

    console.print(f"[bold]Profile:[/bold] {profile.name} (kind={profile.kind})")
    console.print(f"[bold]Command:[/bold] {' '.join(_quote(a) for a in argv)}")

    if dry_run:
        console.print("[dim](dry-run — not submitting)[/dim]")
        return 0

    try:
        submit_fn = submit or _default_submit
        job_id = submit_fn(argv)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        err_console.print(f"[red]error:[/red] sbatch failed: {exc}")
        return 1

    console.print(f"Submitted [bold]job {job_id}[/bold]; waiting for allocation...")

    try:
        node = wait_for_running(
            job_id,
            poll=poll,
            sleep=sleep,
            timeout=wait_timeout,
            interval=poll_interval,
        )
    except RuntimeError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        return 1

    s = sess.Session.new(
        profile=profile.name,
        kind=profile.kind,
        job_id=job_id,
        node=node,
        ports=list(profile.forward),
    )
    saved_at = sess.save(s, session_path)
    console.print(
        f"[green]Session ready[/green] on [bold]{node}[/bold] "
        f"(job {job_id}). State recorded at {saved_at}."
    )
    console.print(
        f"[dim]Hop in with: srun --jobid={job_id} --pty bash[/dim]"
    )
    return 0


def _check_for_existing(session_path: Path | None) -> str:
    """Inspect any existing session.json. Returns one of: 'absent', 'stale', 'running'.

    Side effects:
      - 'stale' → prints a notice and clears the orphan session.json.
      - 'running' → prints an error.
    """
    try:
        existing = sess.load(session_path)
    except sess.SessionError as exc:
        console.print(f"[yellow]Clearing malformed session.json:[/yellow] {exc}")
        sess.clear(session_path)
        return "absent"

    if existing is None:
        return "absent"

    if sess.is_job_alive(existing.job_id):
        err_console.print(
            f"[yellow]A session is already running:[/yellow] "
            f"job {existing.job_id} on {existing.node} "
            f"(profile {existing.profile}).\n"
            f"Run [bold]solx session info[/bold] or [bold]solx session stop[/bold] first."
        )
        return "running"

    console.print(
        f"[dim]Found stale session.json (job {existing.job_id} no longer queued); "
        f"clearing it.[/dim]"
    )
    sess.clear(session_path)
    return "stale"


def session_info(
    *,
    json_output: bool = False,
    session_path: Path | None = None,
) -> int:
    try:
        s = sess.load(session_path)
    except sess.SessionError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        return 1

    if s is None:
        if json_output:
            print("null")
        else:
            err_console.print("No active session (session.json not found).")
        return 1

    if json_output:
        print(json.dumps(asdict(s), indent=2))
    else:
        table = Table(title="solx session", show_header=False, title_justify="left")
        table.add_column("key", style="bold")
        table.add_column("value")
        table.add_row("profile", s.profile)
        table.add_row("kind", s.kind)
        table.add_row("job_id", s.job_id)
        table.add_row("node", s.node)
        table.add_row("ports", ", ".join(str(p) for p in s.ports) or "(none)")
        table.add_row("started_at", s.started_at)
        console.print(table)
    return 0


def session_stop(
    *,
    session_path: Path | None = None,
    scancel: Callable[[str], None] | None = None,
) -> int:
    s = sess.load(session_path)
    if s is None:
        err_console.print("No session to stop (session.json not found).")
        return 0  # idempotent

    scancel_fn = scancel or _default_scancel
    scancel_fn(s.job_id)
    sess.clear(session_path)
    console.print(
        f"Cancelled job [bold]{s.job_id}[/bold]; cleared session state."
    )
    return 0


# ---------------------------------------------------------------------------
# config init / show
# ---------------------------------------------------------------------------


def config_init(
    *,
    force: bool = False,
    config_path: Path | None = None,
) -> int:
    try:
        path = cfg.write_starter(path=config_path, force=force)
    except cfg.ConfigError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        return 2
    console.print(f"Wrote starter profiles to [bold]{path}[/bold].")
    console.print(
        "Edit it, then run [bold]solx config show[/bold] to verify the resolved view."
    )
    return 0


def config_show(
    *,
    json_output: bool = False,
    config_path: Path | None = None,
) -> int:
    try:
        profiles = cfg.load_profiles(path=config_path)
    except cfg.ConfigError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        return 2

    if json_output:
        print(json.dumps(_profiles_to_dict(profiles), indent=2))
        return 0

    if not profiles:
        err_console.print("No profiles defined.")
        return 0

    for name, p in profiles.items():
        table = Table(
            title=f"profile: {name}",
            show_header=False,
            title_justify="left",
        )
        table.add_column("key", style="bold")
        table.add_column("value")
        for f in dataclasses.fields(p):
            if f.name in {"name", "extra"}:
                continue
            table.add_row(f.name, repr(getattr(p, f.name)))
        for k, v in p.extra.items():
            table.add_row(f"extra.{k}", repr(v))
        console.print(table)
    return 0


def _profiles_to_dict(profiles: dict[str, cfg.Profile]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, p in profiles.items():
        body: dict = {}
        for f in dataclasses.fields(p):
            if f.name in {"name", "extra"}:
                continue
            value = getattr(p, f.name)
            if isinstance(value, tuple):
                value = list(value)
            body[f.name] = value
        for k, v in p.extra.items():
            body[k] = v
        out[name] = body
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quote(arg: str) -> str:
    """Shell-quote an argv element only if it contains whitespace or special chars."""
    if not arg or any(ch in arg for ch in " \t\n\"'\\$`*?<>|&;"):
        # Use double quotes so $-escapes inside aren't expanded by the user's
        # shell when they copy-paste, but escape embedded double quotes.
        return '"' + arg.replace('"', '\\"') + '"'
    return arg

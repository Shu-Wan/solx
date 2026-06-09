"""Output layer: human Rich rendering vs machine-readable JSON.

Principle (issue #16 — "CLI design for agents"): a CLI driven by an agent
should not have to know a flag exists to get parseable output. So:

* When stdout is **not a TTY**, data commands emit JSON automatically; on a
  TTY they render Rich tables. The global `--json` flag forces JSON anywhere
  (a human on a terminal gets tables with no flag; the agent passes `--json`).
* All diagnostics, progress, and errors go to **stderr**, so stdout stays a
  clean data channel an agent can parse without stripping noise.
* Interactivity (whether we may *prompt*) is decided by **stdin**, separately
  from the stdout-format decision. A non-interactive session never blocks on
  a confirmation prompt (see `solx.jobs` / `solx.keep`).

`Out` bundles those three decisions plus the two streams so command bodies take
a single object and stay testable: a test builds an `Out` over ``StringIO``
streams with an explicit mode instead of poking globals.

**`rich` stays off the agent path.** On the JSON / non-interactive path
`Out.auto` builds a `_Plain` writer (plain text, markup stripped) instead of a
`rich.Console`, so an agent run (`--json`, or piped output) never imports
`rich` at all. `rich` is imported only when there's a human terminal to render
a table or coloured diagnostic for. Command modules import `rich.table` /
`rich.prompt` lazily for the same reason.
"""
from __future__ import annotations

import json as _json
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from rich.console import Console


# Output mode override. The CLI sets "json" via the global --json flag; None
# means auto-detect from the stdout TTY. "plain" (force human) is supported by
# Out.auto for embedders/tests but has no CLI flag — a human on a terminal
# already gets human output by default, so forcing it isn't worth a flag.
Force = str  # "json" | "plain" | None


# Rich style tags ([red], [/], [bold dim], …). The char class deliberately
# excludes sentence punctuation (commas, quotes) so an interpolated exception
# string like "(at line 11, column 21)" isn't mistaken for markup. A literal
# bracket is written escaped as `\[` in our messages, so it is protected first.
_MARKUP = re.compile(r"\[/?[a-zA-Z0-9 #._]*\]")


def _plain(msg: str) -> str:
    """Strip Rich markup from `msg` for the no-Rich (agent/JSON) path."""
    msg = msg.replace("\\[", "\x00")  # protect escaped literal brackets
    msg = _MARKUP.sub("", msg)
    return msg.replace("\x00", "[")


class _Plain:
    """Minimal stand-in for `rich.Console` on the no-Rich path.

    Exposes just the slice command bodies (and tests) touch — `.print`, which
    strips markup and writes plain text, and `.file` — so nothing imports
    `rich` when output is JSON / agent-facing. Only ever receives diagnostic
    strings (the human table path constructs a real `rich.Console`).
    """

    is_terminal = False

    def __init__(self, file: Any) -> None:
        self.file = file

    def print(self, obj: Any = "") -> None:
        self.file.write(_plain(str(obj)) + "\n")


@dataclass
class Out:
    """A resolved output target: format choice + the two streams.

    * ``json_mode``  — emit JSON on the data channel (stdout) instead of Rich.
    * ``interactive`` — stdin is a TTY, so prompting a human is allowed.
    * ``stdout`` / ``stderr`` — the data and diagnostic writers: a
      ``rich.Console`` in human mode, a ``_Plain`` writer on the agent path.
      Both expose ``.print`` and ``.file``.
    """

    json_mode: bool
    interactive: bool
    stdout: "Console | _Plain"
    stderr: "Console | _Plain"

    @classmethod
    def auto(
        cls,
        *,
        force: Force | None = None,
        stdout: Any | None = None,
        stderr: Any | None = None,
        interactive: bool | None = None,
    ) -> "Out":
        """Build an `Out`, auto-detecting format from the stdout TTY.

        ``force`` (`"json"`/`"plain"`/`None`) overrides the auto-detect; the CLI
        passes `"json"` (global `--json`) or `None`. ``interactive`` defaults to
        whether **stdin** is a TTY. On the JSON path no `rich.Console` is built
        (and `rich` is never imported) — a `_Plain` writer is used instead.
        """
        # TTY-ness for format detection — from a caller-supplied stream/console
        # (tests, embedders) or sys.stdout (production), without importing rich.
        probe = stdout if stdout is not None else sys.stdout
        is_tty = getattr(probe, "is_terminal", None)
        if is_tty is None:
            try:
                is_tty = probe.isatty()
            except (AttributeError, ValueError, OSError):
                is_tty = False

        if force == "json":
            json_mode = True
        elif force == "plain":
            json_mode = False
        else:
            json_mode = not is_tty

        if interactive is None:
            try:
                interactive = sys.stdin.isatty()
            except (ValueError, OSError):
                interactive = False

        so, se = stdout, stderr
        if so is None or se is None:
            if json_mode:
                if so is None:
                    so = _Plain(sys.stdout)
                if se is None:
                    se = _Plain(sys.stderr)
            else:
                from rich.console import Console

                if so is None:
                    so = Console()
                if se is None:
                    se = Console(stderr=True)
        return cls(json_mode=json_mode, interactive=interactive, stdout=so, stderr=se)

    # --- diagnostics: always stderr, never on the JSON stdout stream --------

    def status(self, msg: str) -> None:
        """A progress / context line. Goes to stderr in every mode."""
        self.stderr.print(msg)

    def error(self, msg: str) -> None:
        """An error line. Goes to stderr in every mode."""
        self.stderr.print(msg)

    # --- data channel: stdout -----------------------------------------------

    def json(self, obj: Any) -> None:
        """Write one clean JSON document to stdout (no ANSI, no wrapping)."""
        # Write straight to the underlying file so Rich never injects color
        # or soft-wraps the payload, even under a forced `--json` on a TTY.
        self.stdout.file.write(_json.dumps(obj, indent=2, default=str) + "\n")
        self.stdout.file.flush()

    def human(self, renderable: Any) -> None:
        """Render something to stdout in human mode (Rich table, text, …)."""
        self.stdout.print(renderable)

    def emit(self, *, data: Any, human: Callable[[], Any]) -> None:
        """Emit a result: JSON ``data`` in json mode, else the ``human`` render.

        ``human`` is a thunk so the (possibly expensive) Rich renderable is
        only built when it will actually be shown.
        """
        if self.json_mode:
            self.json(data)
        else:
            rendered = human()
            if rendered is not None:
                self.stdout.print(rendered)

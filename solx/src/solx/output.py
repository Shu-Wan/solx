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

`Out` bundles those three decisions plus the two consoles so command bodies
take a single object and stay testable: a test builds an `Out` over
``StringIO`` consoles with an explicit mode instead of poking globals.
"""
from __future__ import annotations

import json as _json
import sys
from dataclasses import dataclass
from typing import Any, Callable

from rich.console import Console


# Output mode override. The CLI sets "json" via the global --json flag; None
# means auto-detect from the stdout TTY. "plain" (force human) is supported by
# Out.auto for embedders/tests but has no CLI flag — a human on a terminal
# already gets human output by default, so forcing it isn't worth a flag.
Force = str  # "json" | "plain" | None


@dataclass
class Out:
    """A resolved output target: format choice + the two streams.

    * ``json_mode``  — emit JSON on the data channel (stdout) instead of Rich.
    * ``interactive`` — stdin is a TTY, so prompting a human is allowed.
    * ``stdout`` / ``stderr`` — Rich consoles for the data and diagnostic
      channels respectively.
    """

    json_mode: bool
    interactive: bool
    stdout: Console
    stderr: Console

    @classmethod
    def auto(
        cls,
        *,
        force: Force | None = None,
        stdout: Console | None = None,
        stderr: Console | None = None,
        interactive: bool | None = None,
    ) -> "Out":
        """Build an `Out`, auto-detecting format from the stdout TTY.

        ``force`` (`"json"`/`"plain"`/`None`) overrides the auto-detect; the
        CLI passes `"json"` (global `--json`) or `None`. ``interactive``
        defaults to whether **stdin** is a TTY.
        """
        so = stdout or Console()
        se = stderr or Console(stderr=True)
        if force == "json":
            json_mode = True
        elif force == "plain":
            json_mode = False
        else:
            json_mode = not so.is_terminal
        if interactive is None:
            try:
                interactive = sys.stdin.isatty()
            except (ValueError, OSError):
                interactive = False
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

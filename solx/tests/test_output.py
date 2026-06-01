from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from solx.output import Out


def make_out(*, json_mode: bool = False, interactive: bool = True) -> Out:
    so = Console(file=StringIO(), force_terminal=False, width=200)
    se = Console(file=StringIO(), force_terminal=False, width=200)
    return Out(json_mode=json_mode, interactive=interactive, stdout=so, stderr=se)


# ---- force / auto-detect -------------------------------------------------


def test_force_json() -> None:
    out = Out.auto(force="json", stdout=Console(file=StringIO(), force_terminal=True))
    assert out.json_mode is True


def test_force_plain_overrides_non_tty() -> None:
    # Non-TTY stdout would auto-detect JSON; --plain forces human.
    out = Out.auto(force="plain", stdout=Console(file=StringIO(), force_terminal=False))
    assert out.json_mode is False


def test_auto_non_tty_is_json() -> None:
    out = Out.auto(stdout=Console(file=StringIO(), force_terminal=False), interactive=False)
    assert out.json_mode is True


def test_auto_tty_is_human() -> None:
    out = Out.auto(stdout=Console(file=StringIO(), force_terminal=True), interactive=True)
    assert out.json_mode is False


# ---- streams -------------------------------------------------------------


def test_status_goes_to_stderr_not_stdout() -> None:
    out = make_out(json_mode=True)
    out.status("hello")
    assert out.stderr.file.getvalue().strip() == "hello"
    assert out.stdout.file.getvalue() == ""


def test_error_goes_to_stderr() -> None:
    out = make_out(json_mode=True)
    out.error("boom")
    assert "boom" in out.stderr.file.getvalue()
    assert out.stdout.file.getvalue() == ""


def test_json_is_clean_parseable() -> None:
    out = make_out(json_mode=True)
    out.json({"jobid": "123", "state": "RUNNING"})
    payload = out.stdout.file.getvalue()
    assert json.loads(payload) == {"jobid": "123", "state": "RUNNING"}


def test_emit_json_mode() -> None:
    out = make_out(json_mode=True)
    out.emit(data={"n": 1}, human=lambda: "human-text")
    assert json.loads(out.stdout.file.getvalue()) == {"n": 1}


def test_emit_human_mode() -> None:
    out = make_out(json_mode=False)
    out.emit(data={"n": 1}, human=lambda: "human-text")
    assert "human-text" in out.stdout.file.getvalue()
    assert out.stdout.file.getvalue().strip() != '{"n": 1}'
